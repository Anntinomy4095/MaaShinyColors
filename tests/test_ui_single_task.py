"""One-task selection keeps the daily plan and scopes budgets to that run."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class SingleTaskTests(unittest.TestCase):
    def test_selection_menu_and_flags_do_not_change_saved_plan(self):
        body = """
import { readFileSync } from 'node:fs';
import { profileFromPreset, profileToRunConfig, profileSpendFlags, addTaskToPlan } from './ui/components/store.js';
import { singleTaskState, wingOnly } from './ui/components/display.js';
import { rowMenuItems } from './ui/components/task-add.js';
const schema=JSON.parse(readFileSync('ui/fixtures/schema.json','utf8'));
const annotations=JSON.parse(readFileSync('ui/annotations.json','utf8'));
const profile=profileFromPreset(schema,'daily');
const state={schema,annotations,profile,prefs:{developer:false},run:{active:false},expanded:[]};
const original=JSON.stringify(profile);
const selected=singleTaskState(state,'Training');
const config=profileToRunConfig(schema,selected.profile);
let clicked=null;
const menu=rowMenuItems(state,{startRun:(name)=>clicked=name},{name:'Training',index:2,prev:1,next:3});
const button=menu.find(row=>row.label==='仅运行此任务'); button.onSelect();
const active=rowMenuItems({...state,run:{active:true}}, {}, {name:'Training',index:2,prev:1,next:3})
  .find(row=>row.label==='仅运行此任务');
const withWing={...state,profile:addTaskToPlan(schema,profile,'AutoProduce')};
const wing=singleTaskState(withWing,'AutoProduce');
console.log(JSON.stringify({names:config.tasks.filter(t=>t.enabled).map(t=>t.name),
  flags:profileSpendFlags(schema,selected.profile),originalUnchanged:JSON.stringify(profile)===original,
  order:selected.profile.tasks.map(t=>t.name), originalOrder:profile.tasks.map(t=>t.name),
  options:selected.profile.tasks.find(t=>t.name==='Training').option,
  originalOptions:profile.tasks.find(t=>t.name==='Training').option,
  clicked,disabled:button.disabled,runningDisabled:active.disabled,
  wingOnly:wingOnly(wing), unknown:singleTaskState(state,'Unknown'),
  hidden:singleTaskState(state,'Diagnostics')}));
"""
        result = subprocess.run(['node', '--input-type=module', '-e', body], cwd=ROOT,
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['names'], ['Training'])
        self.assertEqual(data['flags'], ['allow_training_spend'])
        self.assertTrue(data['originalUnchanged'])
        self.assertEqual(data['order'], data['originalOrder'])
        self.assertEqual(data['options'], data['originalOptions'])
        self.assertEqual(data['clicked'], 'Training')
        self.assertFalse(data['disabled'])
        self.assertTrue(data['runningDisabled'])
        self.assertTrue(data['wingOnly'])
        self.assertIsNone(data['unknown'])
        self.assertIsNone(data['hidden'])


    def test_resource_read_runs_only_startup_and_the_read(self):
        # 「只读取资源」 (2026-10-26, role-play D6/A8): 主页 → 更新资源 → 只读取资源; never part of the daily 开始
        body = """
import { readFileSync } from 'node:fs';
import { profileFromPreset, profileToRunConfig, profileSpendFlags, applyTaskPlacement } from './ui/components/store.js';
import { singleTaskState, dailyRunState, taskPlacement, startBlockers, taskShown, RESOURCE_TASK } from './ui/components/display.js';
import { updateChoices } from './ui/components/resource-update.js';
const schema=JSON.parse(readFileSync('ui/fixtures/schema.json','utf8'));
const annotations=JSON.parse(readFileSync('ui/annotations.json','utf8'));
const profile=applyTaskPlacement(profileFromPreset(schema,'daily'), taskPlacement(annotations, schema));
const state={schema,annotations,profile,prefs:{developer:false},run:{active:false},expanded:[]};
const enabled=(s)=>profileToRunConfig(schema,s.profile).tasks.filter(t=>t.enabled).map(t=>t.name);
const read=singleTaskState(state,RESOURCE_TASK);
const old={...state,profile:{...profile,tasks:profile.tasks.filter(t=>t.name!==RESOURCE_TASK),removed_tasks:['Startup']}};
console.log(JSON.stringify({read:enabled(read),daily:enabled(dailyRunState(state)),flags:profileSpendFlags(schema,read.profile),
  old:enabled(singleTaskState(old,RESOURCE_TASK)),blockers:startBlockers(read),shown:taskShown(state,RESOURCE_TASK),
  saved:profile.tasks.find(t=>t.name===RESOURCE_TASK).enabled,choice:updateChoices().find(c=>c.key==='resources').action}));
"""
        result = subprocess.run(['node', '--input-type=module', '-e', body], cwd=ROOT,
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['read'], ['Startup', 'ResourceRead'])
        self.assertNotIn('ResourceRead', data['daily'])
        self.assertEqual((data['flags'], data['blockers'], data['shown'], data['saved']), ([], [], False, False))
        self.assertEqual(data['old'], ['Startup', 'ResourceRead'])        # older saved profile, Startup removed
        self.assertEqual(data['choice'], {'label': '只读取资源', 'run': 'resources'})


if __name__ == '__main__':
    unittest.main()
