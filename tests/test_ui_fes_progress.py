"""The Fes preview: per-run Grade count, today's lives shown only; offline runs never count."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from agent.core import fes_allocation as fa, grade_task as gt, match_task as mt

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class FesProgressPageTests(unittest.TestCase):
    def run_js(self, body):
        setup = """
import { readFileSync } from 'node:fs';
import { fesPlan, gradeDailyPlan } from './ui/components/fes-plan.js';
const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));
const now = new Date('2026-10-12T08:00:00Z');
const grade = {name:'GradeFes', enabled:true, option:{'Grade使用参加权':'Yes', 'Grade每日次数':'3'}};
const match = {name:'MatchSkip', enabled:true, option:{'Match只采样':'No', 'Match次数':'全部用完'}};
const reading = (progress, extra = {}) => ({kind:'grade', run_id:'actual', mode:'supervised_daily',
  ts:'2026-10-12T07:00:00Z', readings:{daily_progress:{mode:'grade', target:3, completed:1,
    attempts:1, remaining:2, planned_runs:2, samples_remaining:2, ...progress}}, ...extra});
const state = {schema, profile:{tasks:[grade, match]}, runReadings:[reading({})]};
"""
        result = subprocess.run(["node", "--input-type=module", "-e", setup + body], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_each_run_plays_its_count_and_matches_backend(self):
        rows = self.run_js("""
const rows=[];
for (const mode of ['grade','rehearsal']) for (const target of [1,2,3]) {
  grade.option['Grade模式']=mode==='grade'?'グレードフェス':'フェスリハーサル';
  grade.option['Grade每日次数']=String(target);
  for (const completed of new Set([0,1,target])) {
    state.runReadings=[reading({mode,target,completed,attempts:completed})];
    rows.push({mode,target,completed,plan:fesPlan(state,20-completed,now)});
  }
}
console.log(JSON.stringify(rows));
""")
        for row in rows:
            mode, target, completed = row["mode"], row["target"], row["completed"]
            with self.subTest(mode=mode, target=target, completed=completed):
                config = gt.GradeConfig(mode=mode, runs_per_day=target, live_play=True)
                state = gt.LogState(attempts=completed, verified=completed)
                progress = gt.progress_from_state(gt.per_run_config(config, state), state)
                allocation = fa.Allocation.from_configs(mt.MatchConfig(sample_only=False), config,
                    order=("GradeFes", "MatchSkip"), grade_progress=progress)
                backend = fa.plan(allocation, (20-completed,20), fa.TicketPolicy())
                out = row["plan"]
                self.assertEqual((out["grade"],out["rehearsal"],out["match"],out["total"]),
                                 (backend.grade,backend.rehearsal,backend.match,backend.total))
                self.assertIn(f"本轮 {target} 局", out["summary"])
                if completed:
                    self.assertIn(f"今日已完成 {completed} 局", out["summary"])

    def test_a_completed_day_still_plans_the_full_count(self):
        out = self.run_js("""
grade.option['Grade每日次数']='4';
const changed = fesPlan(state,19,now);
state.runReadings = [reading({completed:3, attempts:3, remaining:0, planned_runs:0, samples_remaining:0})];
grade.option['Grade每日次数']='3';
console.log(JSON.stringify({changed, done:fesPlan(state,17,now)}));
""")
        self.assertEqual((out["changed"]["grade"], out["changed"]["match"]), (4,15))
        self.assertEqual((out["done"]["grade"],out["done"]["match"],out["done"]["errors"]), (3,14,[]))

    def test_modes_game_day_and_offline_runs_do_not_supply_live_completion(self):
        out = self.run_js("""
const wrong = [reading({}, {mode:'offline_replay'}), reading({mode:'rehearsal'}),
  reading({}, {ts:'2026-10-11T19:59:59Z'}), reading({}, {ts:'2026-10-12T09:00:00Z'})];
const unknown = wrong.map(r => gradeDailyPlan({...state,runReadings:[r]},grade,now));
const old = reading({}); delete old.mode;
const history = gradeDailyPlan({...state,runReadings:[old],runs:[{id:'actual',mode:'supervised_daily'}]},grade,now);
const fresh = gradeDailyPlan({...state,runReadings:[reading({}, {ts:'2026-10-11T20:00:00Z'})]},grade,now);
console.log(JSON.stringify({unknown,history,fresh}));
""")
        self.assertTrue(all(r["completed"] is None and not r["known"] for r in out["unknown"]))
        self.assertEqual(out["history"]["completed"], 1)
        self.assertEqual(out["fresh"]["completed"], 1)  # JST 05:00 boundary is inclusive

    def test_manual_mode_and_explicit_attempt_limit_are_visible(self):
        out = self.run_js("""
grade.option['Grade自动演出']='关闭'; const manual=fesPlan(state,19,now);
grade.option['Grade自动演出']='开启'; grade.option['Grade每日次数']='5';
grade.option['Grade采样每日上限']='3'; const limited=fesPlan(state,19,now);
console.log(JSON.stringify({manual,limited}));
""")
        self.assertEqual((out["manual"]["grade"], out["manual"]["match"]), (1,18))
        self.assertEqual((out["limited"]["grade"], out["limited"]["match"]), (3,16))
        self.assertIn("尝试上限 3",out["limited"]["summary"])

    def test_unknown_count_is_labelled_and_five_default_lives_are_not_limited(self):
        out = self.run_js("""
grade.option['Grade每日次数']='5'; state.runReadings=[];
console.log(JSON.stringify(fesPlan(state,20,now)));
""")
        self.assertEqual((out["grade"],out["match"]), (5,15))
        self.assertIsNone(out["daily_progress"]["completed"])
        self.assertIn("本轮 5 局",out["summary"])


if __name__ == "__main__": unittest.main()
