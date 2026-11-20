"""ノウハウ page guidance (2026-10-26 user: 新建 was hard to find, 完成标准 read as jargon, 添加条目 / 目标条目 needed
explanations; then: only the in-game effect and the source tag, no guide copy, shorter lists, tabs in the order of use,
no 「?」): template cards with what they hold, the preview Target marked as unsaved with a one-tap 「用这个目标」, plain 完成条件
options, effect text from the read book details, step marks on the tabs, and OCR-misread 名义 snapped to the 283 roster.
Node + tests/fixtures/ui/fake_dom.mjs; no browser, no game input."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui" / "components"
NODE = shutil.which("node")


def run_node(body, data=None):
    script = ("import './tests/fixtures/ui/fake_dom.mjs';"
              "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
              "const d = await import('./ui/components/knowhow-data.js');"
              "const r = await import('./ui/components/knowhow-rules.js');"
              "const p = await import('./ui/components/knowhow-plan.js');"
              "const w = await import('./ui/components/knowhow-workflow.js');"
              "const walk = (n, f) => { f(n); for (const c of n.childNodes || []) walk(c, f); };"
              "const buttons = (n) => { const out = []; walk(n, (x) => { if (x.tagName === 'BUTTON') out.push(x); }); return out; };"
              "const press = (n, text) => buttons(n).find((b) => b.textContent.includes(text)).listeners.click[0]({ stopPropagation() {} });"
              "const api = () => { const saved = []; return { saved, view: {}, save: (plan) => saved.push(plan), go: (patch) => saved.push({ go: patch }) }; };"
              "const data = JSON.parse(process.argv[1]);" + body)
    result = subprocess.run([NODE, "--input-type=module", "-e", script, json.dumps(data, ensure_ascii=False)],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


@unittest.skipUnless(NODE, "node is required")
class TargetCreationTests(unittest.TestCase):
    def test_preview_target_says_unsaved_and_one_tap_saves_it(self):
        out = run_node(
            "const preview = p.targetFromTemplate('base', 't-preview');\n"
            "const plan = { ...p.upsertTarget(p.migratePlan(null, []), preview), active: preview.id };\n"
            "const ws = { ...w.workflowState({ plan, books: [], team: { idols: [], slots: [] } }), preview: true };\n"
            "const a = api(); const bar = w.targetBar(ws, a);\n"
            "const text = bar.textContent; press(bar, '用这个目标');\n"
            "const saved = a.saved[0];\n"
            "console.log(JSON.stringify({ text, ids: saved.targets.map((t) => t.id), source: saved.targets[0].source, active: saved.active }));")
        self.assertIn("预览·未保存", out["text"])
        self.assertIn("用这个目标", out["text"])
        self.assertIn("换一个", out["text"])
        self.assertEqual(len(out["ids"]), 1)
        self.assertNotIn("t-preview", out["ids"])            # the preview is replaced, not kept beside the saved copy
        self.assertEqual(out["source"], "template:base")
        self.assertEqual(out["active"], out["ids"][0])

    def test_templates_open_behind_new_once_a_target_exists(self):
        out = run_node(
            "const t = p.targetFromTemplate('base');\n"
            "const plan = { ...p.upsertTarget(p.migratePlan(null, []), t), active: t.id };\n"
            "const ws = w.workflowState({ plan, books: [], team: { idols: [], slots: [] } });\n"
            "const count = (n) => { let k = 0; walk(n, (x) => { if ((x.attributes?.class || '').split(' ').includes('kh-tpl')) k += 1; }); return k; };\n"
            "const a = api(); const closed = w.targetsSection(ws, a); press(closed, '＋ 新建目标');\n"
            "const b = api(); b.view.addTarget = true; const open = w.targetsSection(ws, b);\n"
            "console.log(JSON.stringify({ closed: count(closed), go: a.saved[0], open: count(open), text: open.textContent }));")
        self.assertEqual(out["closed"], 0)
        self.assertEqual(out["go"], {"go": {"addTarget": True}})
        self.assertEqual(out["open"], 10)                     # 9 templates + 自定义
        self.assertIn("收起", out["text"])

    def test_no_target_offers_labelled_new_and_template_cards(self):
        out = run_node(
            "const plan = p.migratePlan(null, []);\n"
            "const ws = w.workflowState({ plan, books: [], team: { idols: [], slots: [] } });\n"
            "const full = w.targetBar(ws, api()); const compact = w.targetBar(ws, api(), { compact: true });\n"
            "const list = w.targetsSection(ws, api());\n"
            "const cards = []; walk(full, (x) => { if ((x.attributes?.class || '').split(' ').includes('kh-tpl')) cards.push(x.textContent); });\n"
            "console.log(JSON.stringify({ full: full.textContent, compact: compact.textContent, list: list.textContent, cards }));")
        self.assertIn("选一个", out["full"])                  # 34dda70 show-not-tell: the cards below say the rest
        self.assertIn("＋ 新建目标", out["compact"])
        self.assertIn("还没有目标", out["list"])
        self.assertIn("＋ 新建目标", out["list"])
        self.assertEqual(len(out["cards"]), 9)                # every template, each with what it holds
        self.assertTrue(any(c.startswith("通用底本") and "思い出" in c for c in out["cards"]), out["cards"])
        self.assertTrue(any(c.startswith("S.T.E.P. 素材") and "Lv3" in c for c in out["cards"]), out["cards"])

    def test_saved_target_bar_has_a_labelled_new_button(self):
        out = run_node(
            "const t = p.targetFromTemplate('center');\n"
            "const plan = { ...p.upsertTarget(p.migratePlan(null, []), t), active: t.id };\n"
            "const ws = w.workflowState({ plan, books: [], team: { idols: [], slots: [] } });\n"
            "const a = api(); const bar = w.targetBar(ws, a); press(bar, '＋ 新建');\n"
            "console.log(JSON.stringify({ text: bar.textContent, go: a.saved[0] }));")
        self.assertIn("＋ 新建", out["text"])
        self.assertEqual(out["go"], {"go": {"addTarget": True}})


@unittest.skipUnless(NODE, "node is required")
class PlainWordingTests(unittest.TestCase):
    def test_role_and_strategy_labels_cover_every_choice(self):
        out = run_node(
            "console.log(JSON.stringify({ roles: r.ROLES, short: r.ROLE_SHORT, tip: r.ROLE_TIP, strat: r.STRATEGIES, label: r.STRATEGY_LABEL }));")
        self.assertEqual(set(out["short"]), set(out["roles"]))
        self.assertEqual(set(out["tip"]), set(out["roles"]))
        self.assertEqual(out["label"], {"quick": "必需的都有", "balanced": "必需和优先都有", "optimized": "都有且到理想 Lv"})
        self.assertEqual(set(out["label"]), set(out["strat"]))

    def test_entry_source_tags(self):
        out = run_node(
            "const src = (n) => r.parseEntry(n).source;\n"
            "console.log(JSON.stringify(['スタートダッシュ', '歌姫(G.R.A.D.)', 'ファンの声援(W.I.N.G.)', 'なにか未知'].map((n) => p.entrySource(n, src))));")
        self.assertEqual(out, ["G.R.A.D.", "G.R.A.D.", "W.I.N.G.", None])   # nothing invented

    def test_effect_is_the_most_frequent_in_game_reading(self):
        rows = [
            [{"name": "スタートダッシュ", "level": 3, "effect": "アピール値最大10%UP[経過ターンが短いほど効果UP]"}],
            [{"name": "スタートダッシュ", "level": 5, "effect": "アピール値最大10%UP[経過ターンが短いほど効果UP]"}],
            [{"name": "スタートダッシュ", "level": 2, "effect": "アビール値最大10%UP[経過ターンが短いほど里IP]"}],
            [{"name": "歌姫(G.R.A.D.)", "level": 4, "effect": "Vocalが2000以上のときVocalアピール値UP"}],
            [{"name": "メンタリスト", "level": 1, "effect": "当"}, {"name": "交換数UP(Leader)", "level": 1, "effect": "あいうえ"},
             {"name": "交換数UP(Leader)", "level": 2, "effect": "交換数+1回"}],
            *[[{"name": "Center適正◎", "level": 3, "effect": "フエスユニット編成時、Centerに設定した際"}]] * 3,   # first line only
            *[[{"name": "Center適正◎", "level": 4, "effect": "フェスユニット編成時、Centerに設定した際の基礎ポジションポーナスを+10%"}]] * 2,
            *[[{"name": "スタートダッシュ", "level": 4, "effect": "アピール値最大10%UP[経過ターンが短いほど効果UP]Vocal上限+"}]] * 2]
        books = [{"fingerprint": f"b{i}", "idol": "浅倉透", "rows": r} for i, r in enumerate(rows)]
        out = run_node(
            "const ix = d.effectIndex(data);\n"
            "console.log(JSON.stringify([d.effectFor(ix, { name: 'スタートダッシュ', match: 'exact' }), d.effectFor(ix, { name: '歌姫(', match: 'prefix' }),\n"
            "  d.effectFor(ix, { name: 'メンタリスト', match: 'prefix' }), d.effectFor(ix, { name: '交換数UP(Leader)', match: 'prefix' }),\n"
            "  d.effectFor(ix, { name: 'Leader適正', match: 'prefix' }), d.effectFor(ix, { name: 'Center適正◎', match: 'exact' })]));", books)
        self.assertEqual(out[0], "アピール値最大10%UP[経過ターンが短いほど効果UP]")   # clean beats noisy and a bled next-row name
        self.assertEqual(out[1], "Vocalが2000以上のときVocalアピール値UP")           # prefix entry: a matching read name
        self.assertIsNone(out[2])                                                     # a 1-char fragment is not an effect
        self.assertIsNone(out[3])                                                     # two different single readings: unsure
        self.assertIsNone(out[4])
        self.assertEqual(out[5], "フェスユニット編成時、Centerに設定した際の基礎ポジションボーナスを+10%")   # complete beats first line

    def test_single_and_double_ring_are_separate_items(self):
        out = run_node(
            "const t = p.targetFromTemplate('center');\n"
            "const ringed = (list) => list.filter((i) => /適正|オールラウンダー/.test(i.name)).map((i) => [i.name, i.match, i.group || null, i.role]);\n"
            "const old = { id: 'o', name: 'old', items: [{ name: 'Leader適正', match: 'prefix', role: 'required', min_level: 3, ideal_level: 5 },\n"
            "  { name: '適正', match: 'contains', role: 'optional', min_level: 1, ideal_level: 5 }, { name: 'オールラウンダー◎', match: 'exact', role: 'preferred', min_level: 1, ideal_level: 5 },\n"
            "  { name: 'オールラウンダー', match: 'prefix', role: 'preferred', min_level: 1, ideal_level: 5 }] };\n"
            "const plan = p.migratePlan({ version: 1, targets: [old], active: 'o' });\n"
            "const rec = p.RECOMMENDED_ITEMS.find((g) => g.key === 'attr').items.map((i) => i.name);\n"
            "console.log(JSON.stringify({ center: ringed(t.items), migrated: plan.targets[0].items.map((i) => [i.name, i.match, i.group || null, i.min_level]), rec }));")
        self.assertEqual(out["center"], [["Center適正◎", "exact", "Center適正", "required"], ["Center適正◯", "exact", "Center適正", "required"],
                                         ["オールラウンダー◎", "exact", "オールラウンダー", "preferred"], ["オールラウンダー◯", "exact", "オールラウンダー", "preferred"]])
        self.assertEqual(out["migrated"], [["Leader適正◎", "exact", "Leader適正", 3], ["Leader適正◯", "exact", "Leader適正", 3],
                                           ["適正", "contains", None, 1],                  # any aptitude stays as it was
                                           ["オールラウンダー◎", "exact", None, 1],          # already its own item: not doubled
                                           ["オールラウンダー◯", "exact", "オールラウンダー", 1]])
        self.assertEqual(out["rec"][:6], ["Vocal適正◎", "Vocal適正◯", "Dance適正◎", "Dance適正◯", "Visual適正◎", "Visual適正◯"])

    def test_no_guide_copy_in_entry_texts(self):
        for name in ("knowhow-panel.js", "knowhow-plan.js", "knowhow-workflow.js"):
            text = (UI / name).read_text(encoding="utf-8")
            for phrase in ("也是 S.T.E.P.", "常用", "推荐者", "评价分歧", "几乎任何"):
                self.assertNotIn(phrase, text, (name, phrase))

    def test_no_jargon_left_in_the_page(self):
        for name in ("knowhow-panel.js", "knowhow-workflow.js", "knowhow-rules.js"):
            text = (UI / name).read_text(encoding="utf-8")
            code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
            self.assertNotRegex(code, r'"[^"\n]*完成标准[^"\n]*"', name)
        panel = (UI / "knowhow-panel.js").read_text(encoding="utf-8")
        self.assertNotIn('"达标"', panel)                       # 命中 n/m on 带书, as the help explains


@unittest.skipUnless(NODE, "node is required")
class IdolSnapTests(unittest.TestCase):
    def test_ocr_misreads_snap_to_the_roster_only_when_unique(self):
        out = run_node("console.log(JSON.stringify(data.map((n) => d.snapIdol(n))));",
                       ["篤冬優子", "幽谷鼎子", "斑鳩ル力", "黛冬優子", " 浅倉透 ", "ＡＢＣ", "", None, "透"])
        self.assertEqual(out[:5], ["黛冬優子", "幽谷霧子", "斑鳩ルカ", "黛冬優子", "浅倉透"])
        self.assertEqual(out[5], "ABC")                       # far from every name: kept as read
        self.assertIsNone(out[6])
        self.assertIsNone(out[7])
        self.assertEqual(out[8], "透")                         # a short fragment is never stretched into a name

    def test_unknown_idol_is_not_reported_bringable(self):
        out = run_node(
            "const v = await import('./ui/components/knowhow-views.js');\n"
            "const team = { verified: true, idols: ['浅倉透'], slots: [] };\n"
            "console.log(JSON.stringify([v.bookUsability({ idol: '樋口円香' }, team), v.bookUsability({ idol: '浅倉透' }, team),\n"
            "  v.bookUsability({ idol: '謎の人' }, team)]));")
        self.assertEqual(out, ["ok", "blocked", "unknown"])


class PanelGuideStaticTests(unittest.TestCase):
    def test_tabs_follow_the_order_of_use_with_step_marks(self):
        panel = (UI / "knowhow-panel.js").read_text(encoding="utf-8")
        subpages = re.search(r"export const SUBPAGES = \[(.+?)\];", panel).group(1)
        self.assertEqual(re.findall(r'\["(\w+)", "', subpages), ["library", "goals", "bring"])
        render = panel[panel.index("  render() {"):panel.index("  team(state, data, extras) {")]
        self.assertLess(render.index('h("nav", { class: "kh-subnav"'), render.index("this.statusStrip(ctx)"))   # tabs on top
        self.assertIn('read && saved ? {} : { library: read ? "✓" : "1", goals: saved ? "✓" : "2", bring: "3" }', render)
        for gone in ("helpTip", "guideStrip", "PAGE_HELP"):
            self.assertNotIn(gone, panel)


if __name__ == "__main__":
    unittest.main()
