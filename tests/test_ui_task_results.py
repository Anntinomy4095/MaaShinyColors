"""Task-specific reasons reach the UI without weakening status reconciliation."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from agent.core import training_task, run_control

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class OutcomeDisplayTests(unittest.TestCase):
    def test_claim_text_covers_forwarded_results_and_keeps_task_context(self):
        body = """
import { CLAIM_OUTCOME_TEXT, outcomeText } from './ui/components/outcomes.js';
console.log(JSON.stringify({maps: CLAIM_OUTCOME_TEXT,
  login: outcomeText('claimed', 'MissionsClaim'), business: outcomeText('claimed', 'Business'),
  partial: outcomeText('received_partial', 'PresentClaim')}));
"""
        result = subprocess.run(['node', '--input-type=module', '-e', body], cwd=ROOT,
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        for task, kind in (('MissionsClaim', 'missions_claim'), ('PresentClaim', 'present')):
            self.assertEqual(set(data['maps'][task]), run_control.TASK_OUTCOMES[kind])
        self.assertIn('每日登录', data['login'])
        self.assertIn('营业', data['business'])
        self.assertIn('部分', data['partial'])

    def test_training_explains_real_codes_and_uses_its_own_confirmation_text(self):
        body = """
import { TRAINING_OUTCOME_TEXT, outcomeText } from './ui/components/outcomes.js';
console.log(JSON.stringify({keys:Object.keys(TRAINING_OUTCOME_TEXT),
  training:outcomeText('confirm_mismatch','Training'), shop:outcomeText('confirm_mismatch','Shop'),
  sort:outcomeText('list_sort_unknown','Training'), done:outcomeText('trained','Training'),
  common:outcomeText('spend_uncertain','Training'), unknown:outcomeText('constructor','Training'),
  reserve:outcomeText('skipped_rules: coin_reserve, coin_budget_exceeded','Shop')}));
"""
        result = subprocess.run(['node','--input-type=module','-e',body], cwd=ROOT,
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(set(data['keys']) - training_task.SUCCESS_OUTCOMES - training_task.FAILURE_OUTCOMES)
        self.assertIn('训练确认', data['training'])
        self.assertNotIn('交換確認', data['training'])
        self.assertIn('交換確認', data['shop'])
        self.assertIn('排序', data['sort'])
        self.assertIn('1 张铜', data['done'])
        self.assertIn('可能已消耗', data['common'])
        self.assertEqual(data['unknown'], 'constructor')
        self.assertIn('本轮未购买', data['reserve'])
        self.assertIn('保留值', data['reserve'])
        self.assertIn('本轮预算', data['reserve'])
        self.assertNotIn('coin_', data['reserve'])
        for name in ('task-panel.js','daily-panel.js'):
            code = (ROOT / 'ui/components' / name).read_text(encoding='utf-8')
            self.assertIn('outcomeText(st.reason,', code)


@unittest.skipUnless(shutil.which('dotnet'), 'dotnet not installed')
class NativeTaskReasonTests(unittest.TestCase):
    def test_real_selector_keeps_reconciliation_and_reads_latest_matching_task(self):
        private = ROOT / 'dev_assets/private/ui-task-result-tests'
        private.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as folder:
            path = Path(folder)
            source = ROOT / 'browser_host/Ui/UiTaskResult.cs'
            (path / 'test.csproj').write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
                '<TargetFramework>net10.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings>'
                '<Nullable>enable</Nullable><NuGetAudit>false</NuGetAudit></PropertyGroup><ItemGroup>'
                f'<Compile Include="{source}" Link="UiTaskResult.cs" /></ItemGroup></Project>', encoding='utf-8')
            (path / 'Program.cs').write_text(r'''
using System.Text.Json.Nodes;
using MaaShinyColors.BrowserHost.Ui;
JsonObject Row(string task, string kind, JsonNode? outcome) => new() {
  ["task"]=task, ["kind"]=kind, ["readings"]=new JsonObject {["outcome"]=outcome}
};
void Check(bool ok, string message) { if (!ok) throw new Exception(message); }
var rows = new[] { Row("Training","training","insufficient_tickets"),
  Row("Training","training","list_sort_unknown"), Row("Shop","shop","bought"),
  Row("Training","match","latest_not_skippable"), Row("Training","training","private/path"),
  Row("Training","training",123) };
Check(UiTaskResult.Reason("Training","failed",null,rows)=="list_sort_unknown", "latest training reason");
Check(UiTaskResult.Reason("Shop","succeeded",null,rows)=="bought", "success reason");
var shop = Row("Shop","shop","skipped_rules");
shop["readings"]!["skip_reasons"] = new JsonArray("coin_reserve", "private", "coin_budget_exceeded");
Check(UiTaskResult.Reason("Shop","succeeded",null,new[]{shop})=="skipped_rules: coin_reserve, coin_budget_exceeded", "specific shop reasons");
foreach (var status in new[] {"uncertain","guard_refused","skipped"}) {
  Check(UiTaskResult.Reason("Training",status,"must keep",rows)=="must keep", "preserve severity " + status);
}
Check(UiTaskResult.Reason("Training","failed","fallback",Array.Empty<JsonObject>())=="fallback", "legacy fallback");
Check(UiTaskResult.Reason("Other","failed","fallback",rows)=="fallback", "unknown task");
Check(UiTaskResult.Reason("GradeFes","takeover","fallback",new[]{Row("GradeFes","grade","manual_takeover")})=="manual_takeover", "takeover reason");
Check(UiTaskResult.Reason("MatchSkip","failed",null,new[]{Row("MatchSkip","match","latest_not_skippable")})=="latest_not_skippable", "Match refusal reason");
Check(UiTaskResult.Reason("MissionsClaim","succeeded",null,new[]{Row("MissionsClaim","missions_claim","already_claimed")})=="already_claimed", "login already claimed");
Check(UiTaskResult.Reason("PresentClaim","succeeded",null,new[]{Row("PresentClaim","present","received_partial")})=="received_partial", "partial present receipt");
Console.WriteLine("native task reason checks passed");
''', encoding='utf-8')
            result = subprocess.run(['dotnet','run','--project',str(path / 'test.csproj'),'--verbosity','quiet'],
                cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('native task reason checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
