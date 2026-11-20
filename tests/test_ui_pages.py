"""Page rework 2026-10-08: 主页, 任务 (+添加任务), W.I.N.G., ノウハウ, 设置.

Locks the summary-first / fold-card structure, the pure helpers behind it, the W.I.N.G. rename and that none of the
new controls writes, starts or sends anything on its own.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"

STUBS = "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"


def read(rel):
    return (UI / rel).read_text(encoding="utf-8")


def run_node(script):
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TaskPageJsTests(unittest.TestCase):
    PRELUDE = (STUBS +
               "const m = await import('./ui/components/task-panel.js');"
               "const { readFileSync } = await import('node:fs');"
               "const { profileFromPreset } = await import('./ui/components/store.js');"
               "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
               "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
               "const profile = profileFromPreset(schema, 'daily');"
               "const base = { schema, annotations, profile, prefs: {}, run: { active: false }, taskStatus: {}, expanded: [] };"
               "const task = (n) => schema.tasks.find((t) => t.name === n);"
               "const entry = (n) => profile.tasks.find((t) => t.name === n) || { name: n, enabled: true, option: {} };")

    def test_real_host_readiness_does_not_depend_on_preview_entry(self):
        out = run_node(self.PRELUDE +
                       "const ready={...base,policy:{live_input_enabled:true,risk_reference_resolvable:true},"
                       "host:{pipeline_input_enabled:true,host_guard_reason:null},liveEntry:{reopened:false}};"
                       "console.log(JSON.stringify({ready:m.liveConditions(ready),"
                       "blocked:m.liveConditions({...ready,host:{pipeline_input_enabled:false,host_guard_reason:'desktop_locked'}})}));")
        self.assertTrue(all(row["ok"] for row in out["ready"]))
        self.assertEqual([row["text"] for row in out["blocked"] if not row["ok"]],
                         ["宿主允许 Pipeline 输入", "宿主守卫无拒绝"])

    def test_row_summary_is_one_plain_line(self):
        out = run_node(self.PRELUDE +
                       "const shop = entry('Shop');"
                       "const changed = { ...shop, option: { ...shop.option, '商店跳过券': 'No' } };"
                       "console.log(JSON.stringify({ plain: m.rowSummary(base, task('Shop'), shop).text,"
                       " changed: m.rowSummary(base, task('Shop'), changed), startup: m.rowSummary(base, task('Startup'), entry('Startup')).text }));")
        # Consequential defaults stay visible when the task is folded; 商店 says how many goods and the
        # visible reserve, the tooltip keeps each good.
        self.assertEqual(out["plain"], "今日限定 2 件 · 保留 10万")
        self.assertEqual(out["startup"], "")                                         # 2026-10-28: no settings, no line
        self.assertEqual(out["changed"]["changed"], 1)
        self.assertEqual(out["changed"]["text"], "今日限定 1 件 · 保留 10万")
        self.assertIn("跳过券 10マニー：关", out["changed"]["title"])

    def test_mission_summary_shows_the_selected_claim_scope(self):
        out = run_node(self.PRELUDE +
                       "const claim = entry('MissionsClaim');"
                       "console.log(JSON.stringify({ both: m.rowSummary(base, task('MissionsClaim'), claim).text,"
                       " daily: m.rowSummary(base, task('MissionsClaim'), { ...claim, option: { ...claim.option,"
                       " '任务奖励领取': '开启（仅デイリー一括）' } }).text }));")
        self.assertIn("每日+周常", out["both"])
        self.assertIn("仅每日", out["daily"])

    def test_option_groups_follow_headings_or_common_spend_advanced(self):
        out = run_node(self.PRELUDE +
                       "const g = (n, s = base) => m.optionGroups(s, task(n), entry(n)).map((x) => [x.id, x.label, x.rows.length]);"
                       "const dev = { ...base, prefs: { developer: true } };"
                       "console.log(JSON.stringify({ shop: g('Shop'), training: g('Training'), match: g('MatchSkip'), wing: g('AutoProduce'),"
                       " wingDev: g('AutoProduce', dev), startup: g('Startup') }));")
        self.assertEqual(out["shop"], [])                                           # 2026-10-09: no budget option left
        # 2026-10-11 (UI audit #17): only W.I.N.G. (annotation headings) folds; every other task is one flat list
        self.assertEqual(out["training"], [])
        self.assertEqual(out["match"], [])
        # 2026-10-13 (user): no 「开始」 section (可开新局 is fixed to Yes and hidden, even for developers)
        self.assertEqual([x[1] for x in out["wing"]], ["培养目标", "P 偶像与编队", "行动与 SP", "试镜", "事件与道具", "收尾与结算"])
        self.assertEqual(out["wingDev"][-1][1], "高级")                             # developer-only heading
        self.assertNotIn("开始", [x[1] for x in out["wingDev"]])
        self.assertEqual(out["startup"], [])                                        # nothing to group: plain list

    def test_attention_collects_results_and_blockers(self):
        out = run_node(self.PRELUDE +
                       "const s = { ...base, taskStatus: { Shop: { status: 'uncertain' }, Training: { status: 'succeeded' },"
                       " MatchSkip: { status: 'guard_refused' } } };"
                       "console.log(JSON.stringify(m.taskAttention(s)));")
        self.assertEqual([(i["kind"], i["name"], i.get("status")) for i in out],
                         [("status", "Shop", "uncertain"), ("status", "MatchSkip", "guard_refused")])
        self.assertIn("商店", out[0]["text"])

    def test_panel_wiring(self):
        panel = read("components/task-panel.js")
        # 已核对 moved into the attention card; it still only hides the reminder (ackStatus), never retries.
        self.assertIn('focusKey: `ack-${item.name}`', panel)
        self.assertIn("onclick: () => actions.ackStatus(item.name)", panel)
        self.assertIn("const listParts = [bulk, list, empty, undoBar(state, actions), addTaskEntry(state, actions)];", panel)
        self.assertIn('attention ? h("div", { class: "task-attention" }, attention) : null,', panel)
        self.assertIn("foldSection({ key: `task-${task.name}-${g.id}`", panel)
        self.assertNotIn("bridge.", panel)
        add = read("components/task-add.js")
        self.assertIn("已在方案中 · ${added}", add)
        self.assertIn("(row.added ? addedList : list).append(item);", add)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class WingPageJsTests(unittest.TestCase):
    def test_slot_helpers_and_score_headline(self):
        out = run_node(
            "import { slotLevelText, slotStars } from './ui/components/team-field.js';"
            "import { scoreCardHeadline, scoreCardItems } from './ui/components/wing-score-card.js';"
            "import { readFileSync } from 'node:fs';"
            "const demo = JSON.parse(readFileSync('ui/fixtures/demo-state.json', 'utf8'));"
            "const items = scoreCardItems({ produce: demo.produce });"
            "console.log(JSON.stringify({ s: slotLevelText('S', { level: 60, limit_break: 4 }), p: slotLevelText('P', { level: 60, limit_break: 2 }),"
            " none: slotLevelText('S', null), bad: slotStars(9), head: scoreCardHeadline(items[0].card), n: items.length }));")
        self.assertEqual(out["s"], "Lv60 · 4凸")
        self.assertEqual(out["p"], "2凸")                      # P cards have no Lv
        self.assertEqual(out["none"], "Lv/凸 未填写")           # unknown is said, never 0
        self.assertIsNone(out["bad"])
        self.assertEqual(out["head"], "选 课程 Vo（支援 3 人） · 56.2")  # demo regenerated 2026-10-25 (9c59d8e)
        self.assertEqual(out["n"], 2)


class StaticPageTests(unittest.TestCase):
    def test_pages_css_is_linked_and_uses_only_defined_tokens(self):
        html = read("index.html")
        self.assertIn('<link rel="stylesheet" href="styles/pages.css">', html)
        tokens = set(re.findall(r"(--[\w-]+)\s*:", read("styles/tokens.css")))
        css = read("styles/pages.css")
        local = set(re.findall(r"(--[\w-]+)\s*:", css)) | set(re.findall(r"(--[\w-]+)\s*:", read("styles/app.css")))
        for name in set(re.findall(r"var\((--[\w-]+)", css)):
            self.assertIn(name, tokens | local, name)

    def test_home_is_one_screen_without_tile_grids(self):
        # 2026-10-09: hero (today + reset + 开始日常) → attention → checklist → resource strip → weekly; no 2 × 2 tiles.
        home = read("components/home-panel.js")
        for needle in ("hero(state, actions,", 'ribbon("已选任务"', 'ribbon("资源"', 'ribbon("本周与进度")', "gachaBanner(gacha)",
                       "resIcon(state, c.id", "cardsToConfirm(cardEntries("):
            self.assertIn(needle, home)
        for gone in ("summaryTile", "sum-grid", "statCard("):
            self.assertNotIn(gone, home)
        self.assertEqual(set(re.findall(r"actions\.(\w+)\(", home)), {"setTab", "startRun", "ackStatus", "openTask", "closeNote"})
        self.assertNotIn("free-draw", home)        # 2026-10-12: the manual 免费十连 tick is gone (read-only ガシャ reminder)

    def test_no_page_has_the_forced_four_tile_header(self):
        # 「每个tab不要都像资源一样强行加四个格子在最上方」. Only the 资源 tab keeps its compact stat strip.
        for rel in ("components/home-panel.js", "components/settings-panel.js",
                    "components/knowhow-panel.js", "components/task-panel.js", "components/common.js"):
            text = read(rel)
            self.assertNotIn("summaryTile", text, rel)
            self.assertNotIn("sum-grid", text, rel)
        self.assertNotIn(".sum-grid", read("styles/pages.css"))
        self.assertIn("factLine([", read("components/knowhow-panel.js"))

    def test_settings_live_card_is_read_only(self):
        panel = read("components/settings-panel.js")
        live = panel[panel.index("function liveCard(state)"):panel.index("class SettingsPanel")]
        self.assertNotIn("actions.", live)
        self.assertNotIn("onclick", live)
        for key in ("settings-look", "settings-notify", "settings-live", "settings-dev", "settings-about"):
            self.assertIn(f'key: "{key}"', panel)
        self.assertIn("hint: NOTIFY_HINT[key]", panel)

    def test_wing_rename_everywhere_in_the_ui(self):
        for rel in ("components/header.js", "components/settings-panel.js", "annotations.json",
                    "fixtures/schema.json", "fixtures/mock-bundle.js"):
            self.assertNotIn("W.I.N.G. T.E.", read(rel), rel)
        task = json.loads((ROOT / "assets" / "tasks" / "auto_produce.json").read_text(encoding="utf-8"))["task"][0]
        self.assertEqual(task["label"], "W.I.N.G.")
        self.assertEqual(task["name"], "AutoProduce")          # internal identifier unchanged

    def test_knowhow_folds_and_first_page(self):
        panel = read("components/knowhow-panel.js")
        self.assertIn("const LIBRARY_PAGE = 30;", panel)
        for key in ("kh-pending", "kh-review", "kh-wanted", "kh-catalog", "kh-coverage"):
            self.assertIn(f'key: "{key}"', panel)


if __name__ == "__main__":
    unittest.main()
