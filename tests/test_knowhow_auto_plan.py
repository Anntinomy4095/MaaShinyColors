"""ノウハウ条目 -> Auto 剧本 (agent/core/knowhow_auto_plan.py); synthetic books, the committed source table."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from agent.core import knowhow_auto_plan as kp
from agent.core.knowhow_books import KnowhowBookRecord, WantedItem

ROOT = Path(__file__).resolve().parents[1]


def book(*rows):
    return KnowhowBookRecord.from_dict({"title": "【x】櫻木真乃", "rows": [{"name": n, "level": lv} for n, lv in rows]})


def want(name, match="exact", level=1):
    return WantedItem(name, match, level)


class KnowhowAutoPlanTests(unittest.TestCase):
    def test_source_table_is_well_formed(self):
        sources = kp.load_sources()
        self.assertGreater(len(sources), 70)
        for s in sources:
            self.assertTrue(set(s.scenarios) <= {*kp.AUTO_SCENARIOS, "HALO"}, s.entry)
            self.assertTrue(set(s.hirameki) <= {*kp.COLORS, "ALL"}, s.entry)
            self.assertIn(s.match, ("exact", "prefix"))
        self.assertEqual(set(kp.HIRAMEKI.values()), set(kp.COLORS))

    def test_classify_each_status(self):
        sources = kp.load_sources()
        books = [book(("スロースターター", 5), ("思い出(ノウハウ)+", 2))]
        status = {w.name: kp.classify(w, books, sources)["status"] for w in (
            want("スロースターター"), want("思い出(ノウハウ)+", level=3), want("思い出(ノウハウ)++"),
            want("メンタリスト", "prefix"), want("施設効率相談"), want("昂揚", "prefix"), want("G.R.A.D.決勝"),
            want("どこにもない"))}
        self.assertEqual(status, {"スロースターター": "owned", "思い出(ノウハウ)+": "auto", "思い出(ノウハウ)++": "auto",
                                  "メンタリスト": "manual", "施設効率相談": "manual", "昂揚": "auto_unknown",
                                  "G.R.A.D.決勝": "not_on_books", "どこにもない": "unknown"})
        # 思い出(ノウハウ): the wiki's Auto routes first
        self.assertEqual(kp.classify(want("思い出(ノウハウ)++"), [], sources)["scenarios"], ["LP", "FANS"])

    def test_plan_picks_the_scenario_with_most_missing_auto_entries(self):
        books = [book(("スロースターター", 5))]
        mine = [want("スロースターター"), want("思い出(ノウハウ)++"), want("メンタリスト", "prefix"), want("施設効率相談")]
        result = kp.plan(mine, books)
        self.assertEqual((result["scenario"], result["targets"], result["colors"]), ("LP", ["思い出(ノウハウ)++"], {}))
        grad = kp.plan([want("スタートダッシュ"), want("アピールUP(思い出低)"), want("思い出(ノウハウ)++")], [])
        self.assertEqual(grad["scenario"], "GRAD")
        self.assertEqual(grad["targets"], ["スタートダッシュ", "アピールUP(思い出低)"])
        # Da3 Vi3 + Vo1 Da1 Vi1 Me2 over five supports
        self.assertEqual(grad["colors"], {"Da": 2, "Vi": 2, "Me": 1})
        none = kp.plan([want("メンタリスト", "prefix"), want("施設効率相談")], [])
        self.assertIsNone(none["scenario"])

    def test_support_colors(self):
        self.assertEqual(kp.support_colors({"Vo": 3, "Me": 3}), {"Vo": 3, "Me": 2})
        self.assertEqual(kp.support_colors({"Vo": 1, "Da": 1, "Vi": 1, "Me": 2}), {"Vo": 1, "Da": 1, "Vi": 1, "Me": 2})
        self.assertEqual(kp.support_colors({"ALL": 3}), {})
        self.assertEqual(sum(kp.support_colors({"Vo": 2, "Da": 2, "Vi": 2, "Me": 2}).values()), 5)

    def test_ui_preview_matches_python(self):
        # ui/components/knowhow-auto-plan.js previews the same plan in the Auto panel (剧本「按目标词条」)
        wanted = [want("思い出(ノウハウ)++"), want("スタートダッシュ"), want("アピールUP(思い出低)"), want("メンタリスト", "prefix"),
                  want("昂揚", "prefix"), want("施設効率相談+"), want("スロースターター"), want("G.R.A.D.決勝")]
        rows = [("スロースターター", 5), ("アピールUP(思い出低)", 1)]
        script = (
            "import SOURCES from './ui/components/knowhow-sources.js';"
            "import { targetPlan } from './ui/components/knowhow-auto-plan.js';"
            "const [wanted, rows] = JSON.parse(process.argv[1]);"
            "console.log(JSON.stringify({ sources: SOURCES, plan: targetPlan(wanted, [{ rows }]) }));")
        args = json.dumps([[w.to_dict() for w in wanted], [{"name": n, "level": lv} for n, lv in rows]], ensure_ascii=False)
        result = subprocess.run(["node", "--input-type=module", "-e", script, args], cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        table = json.loads(kp.SOURCES_PATH.read_text(encoding="utf-8"))["entries"]
        self.assertEqual(out["sources"], [[r["entry"], r["match"], r["scenarios"], r["auto"], r["transferable"],
                                           r.get("prefer") or []] for r in table])
        py = kp.plan(wanted, [book(*rows)])
        self.assertEqual((out["plan"]["scenario"], out["plan"]["targets"]), (py["scenario"], py["targets"]))
        self.assertEqual([(e["name"], e["status"], e["scenarios"]) for e in out["plan"]["entries"]],
                         [(e["name"], e["status"], e["scenarios"]) for e in py["entries"]])

    def test_panel_shows_the_planned_scenario(self):
        # the scenario field with 「按目标词条」: the planned scenario and its entries; what Auto cannot find in the tooltip
        script = (
            "import './tests/fixtures/ui/fake_dom.mjs';"
            "import { readFileSync } from 'node:fs';"
            "import { optionField } from './ui/components/option-field.js';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "const all = (n, out = []) => { for (const c of n.childNodes || []) { if (c.tagName) { out.push(c); all(c, out); } } return out; };"
            "const show = async (wanted, value) => {"
            "  const state = { schema, annotations, bridgeKind: 'mock', prefs: { knowhow_wanted: wanted } };"
            "  const node = optionField({ state, key: 'Auto核对剧本', value, disabled: false, onChange: () => {}, focusKey: 'k' });"
            "  await new Promise((r) => setTimeout(r, 200));"
            "  const p = all(node).find((e) => String(e.attributes?.class || '').includes('target-preview'));"
            "  return p ? { text: p.textContent, titles: all(p).map((e) => e.attributes?.title).filter(Boolean) } : null; };"
            "console.log(JSON.stringify([await show([{ name: 'Visual適正◎' }, { name: 'メンタリスト', match: 'prefix' },"
            "  { name: '施設効率相談' }], '按目标词条'), await show([{ name: 'Visual適正◎' }], 'G.R.A.D.')]));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        target, other = json.loads(result.stdout)
        self.assertIsNone(other)
        self.assertEqual(target["text"], "G.R.A.D.：Visual適正◎ · Auto 拿不到 2")    # the demo library lacks Visual適正◎
        self.assertEqual(target["titles"], ["メンタリスト（手动 G.R.A.D.）\n施設効率相談（say \"Halo\"）"])


if __name__ == "__main__":
    unittest.main()
