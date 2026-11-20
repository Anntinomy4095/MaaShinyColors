"""Standalone offline CLI contracts with synthetic input, not account data."""
from contextlib import redirect_stderr
from dataclasses import asdict, replace
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from tools.true_end_limit_break import main, render_report, run
from test_true_end_limit_break import fixture


def input_value():
    cards, owned, growth, cfg = fixture()
    return {"schema_version": 1, "cards": [asdict(c) for c in cards.values()],
            "inventory": [asdict(o) for o in owned.values()], "p_card": "p",
            "baseline": {"active": ["s0", "s1", "s2", "s3"], "guest": "guest"},
            "growth": asdict(growth), "search": asdict(cfg), "battle_scenarios": []}


class LimitBreakCliTests(unittest.TestCase):
    def test_existing_format_fixed_team_and_common_seed(self):
        result = run(input_value(), "s0")
        self.assertEqual(result["team"]["active"], ["s0", "s1", "s2", "s3"])
        self.assertEqual([m["mode"] for m in result["modes"]], ["FIXED_LEVEL", "AT_LB_CAP"])
        self.assertTrue(all(c["true_end_probability"] is None for m in result["modes"] for c in m["cases"]))
        self.assertEqual(len({c["seed"] for m in result["modes"] for c in m["cases"]}), 1)

    def test_baseline_required_no_selection_defaults(self):
        value = input_value()
        del value["baseline"]
        with self.assertRaisesRegex(ValueError, "baseline"):
            run(value, "s0")

    def test_ownership_level_and_break_never_default(self):
        for key in ("level", "limit_break"):
            value = input_value()
            del value["inventory"][1][key]
            with self.assertRaises(ValueError):
                run(value, "s0")

    def test_unique_strategy_and_team_target_required(self):
        value = input_value()
        value["search"]["strategies"] = ["DA", "VO"]
        with self.assertRaisesRegex(ValueError, "唯一策略"):
            run(value, "s0")
        with self.assertRaisesRegex(ValueError, "S卡"):
            run(input_value(), "p")

    def test_report_partial_and_null_do_not_claim_full_mechanics(self):
        value = input_value()
        value["cards"][1]["unsupported"] = ["unsupported-synthetic"]
        result = run(value, "s0", allow_partial=True)
        report = render_report(result)
        self.assertIn("ESTIMATED_WITH_GAPS", report)
        self.assertIn("仅评估已支持机制", report)
        self.assertIn("不代表完整机理", report)
        self.assertIn("True End概率始终为null", report)
        self.assertIn("| 4 | 60 |", report)
        self.assertIn("| 4 | 80 |", report)
        blocked = run(value, "s0")
        self.assertIn("NEEDS_DATA | null | null", render_report(blocked))

    def test_main_external_database_and_two_outputs(self):
        value = input_value()
        definitions = value.pop("cards")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, database, output, report = [root / n for n in ("input.json", "cards.json", "result.json", "report.md")]
            source.write_text(json.dumps(value), encoding="utf-8")
            database.write_text(json.dumps({"cards": definitions}), encoding="utf-8")
            code = main(["--input", str(source), "--cards", str(database), "--target-card", "s0",
                         "--fixed-level", "60", "--json-output", str(output), "--markdown-output", str(report)])
            self.assertEqual(code, 0)
            actual = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(actual["modes"][0]["cases"][4]["status"], "ESTIMATED")
            self.assertIn("同卡0–4凸", report.read_text(encoding="utf-8"))

    def test_invalid_main_never_writes_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / "input.json", root / "result.json"
            source.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
            error = StringIO()
            with redirect_stderr(error):
                code = main(["--input", str(source), "--target-card", "s0", "--json-output", str(output)])
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())
            self.assertIn("baseline", error.getvalue())

    def test_duplicate_database_card_rejected(self):
        value = input_value()
        with self.assertRaisesRegex(ValueError, "重复卡定义"):
            run(value, "s0", extra_cards=[value["cards"][0]])


if __name__ == "__main__":
    unittest.main()
