"""ANN-49 (UI review U17/U18/U20/U21, 2026-10-12): named plans + save indicator, 上次读取 / 更新资源, knowhow empty-library
copy, outcome text. Offline: node-only module checks and static source checks; no host, no game, no input."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
PRELUDE = ("import './tests/fixtures/ui/fake_dom.mjs';"
           "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
           "import { readFileSync } from 'node:fs';"
           "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));")


def run_node(body):
    result = subprocess.run(["node", "--input-type=module", "-e", PRELUDE + body], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class NamedPlanTests(unittest.TestCase):
    def test_save_overwrite_remove_and_name_rules(self):
        out = run_node(
            "const P = await import('./ui/components/plans.js');"
            "const S = await import('./ui/components/store.js');"
            "const base = S.profileFromPreset(schema, 'daily');"
            "const a = P.upsertPlan([], base, '  只练彩排 ', 1);"
            "const b = P.upsertPlan(a.plans, base, '只练彩排', 2);"
            "const c = P.upsertPlan(b.plans, base, '培养');"
            "const bad = ['', 'x'.repeat(40), 'a/b', 'a<b', '..\\\\x'].map((n) => P.upsertPlan([], base, n).error ? 1 : 0);"
            "let many = []; for (let i = 0; i < 14; i++) many = (P.upsertPlan(many, base, 'p' + i).plans || many);"
            "console.log(JSON.stringify({ a: a.name, replaced: b.replaced, n: c.plans.length, bad, many: many.length,"
            "  removed: P.removePlan(c.plans, '培养').map((p) => p.name), cleaned: P.cleanPlans([{ name: 'x', tasks: [] }, { name: 'x', tasks: [] }, { name: '', tasks: [] }, 5]).length }));")
        self.assertEqual(out["a"], "只练彩排")
        self.assertTrue(out["replaced"])
        self.assertEqual(out["n"], 2)
        self.assertEqual(out["bad"], [1, 1, 1, 1, 1])
        self.assertEqual(out["many"], 12)  # capped
        self.assertEqual(out["removed"], ["只练彩排"])
        self.assertEqual(out["cleaned"], 1)

    def test_plan_applies_through_the_sanitiser_and_detects_changes(self):
        out = run_node(
            "const P = await import('./ui/components/plans.js');"
            "const S = await import('./ui/components/store.js');"
            "const base = S.profileFromPreset(schema, 'daily');"
            "const changed = { ...base, tasks: base.tasks.map((t) => (t.name === 'Shop' ? { ...t, enabled: !t.enabled } : t)) };"
            "const { plans } = P.upsertPlan([], changed, '我的');"
            "const hostile = { ...plans[0], tasks: [...plans[0].tasks, { name: 'Evil', enabled: true, option: { x: 'y' } }] };"
            "const applied = P.profileFromPlan(schema, hostile);"
            "console.log(JSON.stringify({ dirty: P.planDirty(base, plans[0]), clean: P.planDirty(changed, plans[0]),"
            "  noEvil: !applied.tasks.some((t) => t.name === 'Evil'), same: P.planDirty(applied, plans[0]),"
            "  shop: [applied.tasks.find((t) => t.name === 'Shop').enabled, changed.tasks.find((t) => t.name === 'Shop').enabled] }));")
        self.assertTrue(out["dirty"])
        self.assertFalse(out["clean"])
        self.assertTrue(out["noEvil"])
        self.assertFalse(out["same"])
        self.assertEqual(out["shop"][0], out["shop"][1])

    def test_indicator_shows_failure_with_retry_and_unsaved_plan_changes(self):
        out = run_node(
            "const P = await import('./ui/components/plans.js');"
            "const S = await import('./ui/components/store.js');"
            "const base = S.profileFromPreset(schema, 'daily');"
            "const plan = P.snapshotOf(base, '方案');"
            "const text = (nodes) => nodes.map((n) => n.textContent).join('|');"
            "const state = (extra) => ({ profile: base, plans: [plan], activePlan: '方案', run: { active: false }, saveStatus: { state: 'saved' }, ...extra });"
            "const dirtyProfile = { ...base, tasks: base.tasks.map((t, i) => (i === 0 ? { ...t, enabled: !t.enabled } : t)) };"
            "console.log(JSON.stringify({ saved: text(P.saveIndicator(state(), {})),"
            "  failed: text(P.saveIndicator(state({ saveStatus: { state: 'failed', error: 'x' } }), {})),"
            "  dirty: text(P.saveIndicator(state({ profile: dirtyProfile }), {})) }));")
        self.assertEqual(out["saved"], "")                                           # 2026-10-28: saved needs no line
        self.assertIn("保存失败", out["failed"])
        self.assertIn("重试", out["failed"])
        self.assertEqual(out["dirty"], "更新方案")

    def test_app_wires_undoable_reset_and_plans(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        for needle in ("replaceProfile(", 'label: "撤销"', "selectPlan:", "savePlanAs:", "deletePlan:", "retrySave:", 'state: "failed"'):
            self.assertIn(needle, app, needle)
        panel = (UI / "components" / "task-panel.js").read_text(encoding="utf-8")
        self.assertIn("另存为", panel)
        self.assertIn("我的方案", panel)
        self.assertIn("saveIndicator(state, actions)", panel)
        # The host profile sanitiser drops unknown keys, so named plans must not travel in profile.save.
        persist = app[app.index("const persist = () => {"):app.index("const replaceProfile")]
        self.assertNotIn("plans", persist)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ResourceUpdateTests(unittest.TestCase):
    def test_last_read_summary_and_choices(self):
        out = run_node(
            "const R = await import('./ui/components/resource-update.js');"
            "const now = Date.parse('2026-10-12T12:00:00');"
            "const iso = (h) => new Date(now - h * 3600e3).toISOString();"
            "const none = R.lastReadSummary([{ id: 'money', count: null, at: null }], now);"
            "const fresh = R.lastReadSummary([{ id: 'money', count: 5, at: iso(1) }, { id: 'fes', count: 3, at: iso(9) }], now);"
            "const old = R.lastReadSummary([{ id: 'money', count: 5, at: iso(30) }], now);"
            "console.log(JSON.stringify({ none, fresh, old, age: R.readAge(iso(3), now), bad: R.readAge('x', now), choices: R.updateChoices().map((c) => [c.key, c.action?.tab || c.action?.run || null]) }));")
        self.assertEqual(out["none"], {"at": None, "text": "未读取", "stale": False, "older": 0})
        self.assertIn("1 小时前", out["fresh"]["text"])
        self.assertFalse(out["fresh"]["stale"])
        # 2026-10-12: the home page shows freshness only above the resources; older readings are counted there
        self.assertEqual(out["fresh"]["older"], 1)
        self.assertNotIn("较旧", out["fresh"]["text"])                             # older readings: the is-stale mark
        self.assertTrue(out["old"]["stale"])
        self.assertIn("3 小时前", out["age"])
        self.assertEqual(out["bad"], "")
        # Three clearly different things; 只读取资源 is an ordinary run.start (no new host command).
        self.assertEqual(out["choices"], [["page", None], ["resources", "resources"], ["cards", "resources"]])

    def test_home_shows_last_read_and_entry_without_a_new_bridge_request(self):
        home = (UI / "components" / "home-panel.js").read_text(encoding="utf-8")
        self.assertIn("lastReadSummary(counters", home)
        self.assertIn("更新资源", home)
        self.assertIn("尚未读取", home)
        self.assertNotIn("home-counter-age", home)      # no per-resource orange line under each value
        self.assertNotIn("bridge.request", home)
        update = (UI / "components" / "resource-update.js").read_text(encoding="utf-8")
        self.assertNotIn(".request(", update)
        for word in ("游戏页刷新", "资源读取", "持有卡扫描"):
            self.assertIn(word, update)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class CopyPassTests(unittest.TestCase):
    def test_outcome_map_has_what_happened_consumed_next_text(self):
        out = run_node(
            "const O = await import('./ui/components/outcomes.js');"
            "console.log(JSON.stringify({ a: O.outcomeText('played_reported'), b: O.outcomeText('progress_stale: 5s'), c: O.outcomeText('unknown_code_x'),"
            "  d: O.outcomeText('latest_not_skippable'), n: Object.keys(O.OUTCOME_TEXT).length }));")
        self.assertIn("消耗", out["a"])
        self.assertIn("（5s）", out["b"])
        self.assertEqual(out["c"], "unknown_code_x")
        self.assertIn("最新关不能跳过", out["d"])
        self.assertGreater(out["n"], 100)

    def test_player_text_has_no_developer_terms(self):
        banned = {"守卫拒绝", "宿主未连接", "待切换动作表", "需更新宿主", "待监督校准"}
        for path in list((UI / "components").glob("*.js")) + [UI / "app.js"]:
            code = chr(10).join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith(("//", "*", "/*")))
            for word in banned:
                self.assertFalse(word in code, f"{path.name}: {word}")
        # 未观测 is replaced by 尚未读取 in rendered strings (comments may keep the old term).
        for name in ("home-panel.js", "resource-panel.js"):
            text = (UI / "components" / name).read_text(encoding="utf-8")
            code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("//", "*", "/*")))
            self.assertNotIn('"未观测"', code, name)

    def test_knowhow_empty_library_leads_with_read_and_new_books(self):
        panel = (UI / "components" / "knowhow-panel.js").read_text(encoding="utf-8")
        scan = (UI / "components" / "knowhow-scan.js").read_text(encoding="utf-8")
        self.assertIn("尚未读取书库", panel)
        self.assertIn("`新获得 ${data.pending.length} 本`", panel)
        # 完全一致 / 前缀 / 包含 live behind 「更多」 in the item editor
        more = panel.index('details("更多"')
        self.assertGreater(panel.index('"名称匹配方式"'), more)
        self.assertNotIn("待切换动作表", scan)
        self.assertIn("此功能暂不可用：需要更新程序", scan)
        # The template is optional: nothing is applied silently when nothing was saved.
        self.assertRegex(panel, r"const wanted = draft \? \[\] : wantedList\(state\)")
        self.assertNotIn("kh-tpl-save", panel)


if __name__ == "__main__":
    unittest.main()
