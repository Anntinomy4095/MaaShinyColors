"""W.I.N.G. 本周评分卡: wing_score.score_card payload, its JS mirror and the synthetic preview fixture.
Offline; no game, no host, no input."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from agent.core import wing_score as sc
from tests.test_wing_score import rooms
from tests.test_wing_te_rules import run_state
from tools import wing_score_card_demo

ROOT = Path(__file__).resolve().parent.parent


class ScoreCardTests(unittest.TestCase):
    def test_groups_partition_the_terms(self):
        names = [t for _, _, terms in sc.TERM_GROUPS for t in terms]
        self.assertEqual(sorted(names), sorted(sc.TERMS))
        self.assertEqual(len(names), len(set(names)))

    def test_card_rows_sum_to_the_scores_and_mark_the_choice(self):
        week = run_state(2, 5, 12_000, 220, trend_vo=2, options=rooms(), stamina=41, trouble_rate=None)
        result = sc.score_week(week, "Vo")
        card = sc.score_card(result.to_dict())
        self.assertEqual([r["id"] for r in card["rows"]], [c.id for c in result.ranked])
        for row in card["rows"]:
            self.assertAlmostEqual(sum(row["groups"].values()), row["score"], places=2)
        self.assertEqual([r["id"] for r in card["rows"] if r["chosen"]], [result.choice.id])
        capped = [e for e in card["excluded"] if e["reason"] == "trouble_hard_cap"]
        self.assertTrue(capped and all(e["trouble"] == 25.0 for e in capped))
        self.assertIn("硬上限", capped[0]["reason_text"])
        self.assertTrue(card["summary"].startswith("选 休息"))

    def test_card_from_a_policy_plan(self):
        from agent.core import wing_policy as wp
        week = run_state(2, 5, 12_000, 220, trend_vo=2, options=rooms(), stamina=90, trouble_rate=None)
        decision = wp.decide(week, "vo_score", "true_end")
        chosen = decision.option_id or decision.audition_id
        card = sc.score_card(decision.plan, chosen=chosen)
        self.assertEqual(card["rule"], decision.plan["score_rule"])
        self.assertEqual([r["id"] for r in card["rows"] if r["chosen"]], [chosen])

    def test_preview_fixture_is_the_offline_output(self):
        demo = json.loads((ROOT / "ui" / "fixtures" / "demo-state.json").read_text(encoding="utf-8"))
        self.assertEqual(demo["produce"]["score_cards"], wing_score_card_demo.demo_cards())
        first, second = demo["produce"]["score_cards"]
        self.assertEqual(first["card"]["chosen"], "lesson_vo_3sup")
        self.assertEqual(second["card"]["chosen"], "rest")
        self.assertIn("非实机", first["source"])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ScoreCardJsTests(unittest.TestCase):
    def run_node(self, body):
        script = ("import * as C from './ui/components/wing-score-card.js';"
                  "import { readFileSync } from 'node:fs';"
                  "const demo = JSON.parse(readFileSync('ui/fixtures/demo-state.json', 'utf8'));" + body)
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_js_groups_mirror_python(self):
        out = self.run_node("console.log(JSON.stringify(C.TERM_GROUPS));")
        self.assertEqual(out, [[k, label, list(terms)] for k, label, terms in sc.TERM_GROUPS])

    def test_js_cleaner_keeps_the_fixture_and_rejects_junk(self):
        out = self.run_node(
            "const cards = demo.produce.score_cards.map((c) => C.cleanScoreCard(c.card));"
            "console.log(JSON.stringify({ rows: cards.map((c) => c.rows.length), chosen: cards.map((c) => c.rows.find((r) => r.chosen)?.id),"
            " excluded: cards.map((c) => c.excluded.map((e) => e.reason)), rule: cards[1].rule,"
            " junk: [C.cleanScoreCard(null), C.cleanScoreCard({ version: 2, rows: [] }), C.cleanScoreCard({ version: 1, rows: 'x' })],"
            " bad: C.cleanScoreCard({ version: 1, rows: [{ id: 'x', kind: 'nap' }, { id: 'y', kind: 'rest', score: 'NaN', groups: { risk: 'z' } }] }),"
            " future: C.FUTURE_CONTROLS }));")
        self.assertEqual(out["chosen"], ["lesson_vo_3sup", "rest"])
        self.assertEqual(out["rows"], [11, 6])
        self.assertEqual(out["excluded"][0], ["gain_unknown"])
        self.assertIn("trouble_hard_cap", out["excluded"][1])
        self.assertEqual(out["junk"], [None, None, None])
        self.assertEqual([r["id"] for r in out["bad"]["rows"]], ["y"])
        self.assertIsNone(out["bad"]["rows"][0]["score"])
        self.assertEqual(out["bad"]["rows"][0]["groups"]["risk"], 0)
        self.assertEqual(len(out["future"]), 3)

    def test_panel_wires_the_card_read_only(self):
        # The W.I.N.G. tab (produce-panel.js) was folded into the task page; the live run shows this week's scored
        # options in live-panel.js (「本周行动」). The card module stays a read-only preview.
        panel = (ROOT / "ui" / "components" / "live-panel.js").read_text(encoding="utf-8")
        self.assertIn("decisionCard(w.decision)", panel)
        card = (ROOT / "ui" / "components" / "wing-score-card.js").read_text(encoding="utf-8")
        for word in ("bridge", "send(", "postMessage", "run.start", "input"):
            self.assertNotIn(word, card)


if __name__ == "__main__":
    unittest.main()
