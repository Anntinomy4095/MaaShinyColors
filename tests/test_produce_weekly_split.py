"""周常 split out of W.I.N.G. T.E.: saved UI profiles with 「Produce目标」=「周常」 move to the separate
WeeklyProduce task; renamed cases keep the user's choice. JS (ui/components/store.js) and Python (tools/ui_schema.py)
must agree. Offline; no game, no host, no input."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from tools import run_tasks, ui_schema

ROOT = Path(__file__).resolve().parent.parent


def legacy_profile(enabled=True, removed=()):
    return {"schema_version": 1, "preset": None, "removed_tasks": list(removed), "tasks": [
        {"name": "Shop", "enabled": True, "option": {}},
        {"name": "AutoProduce", "enabled": enabled, "option": {"Produce目标": "周常", "Produce难度": "Middle",
                                                                "Produce休息阈值": "25%", "ProduceP道具": "手帐+羁绊"}},
        {"name": "Business", "enabled": True, "option": {}},
    ]}


class MigrationPythonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = ui_schema.export_schema()

    def test_weekly_goal_moves_to_the_separate_task(self):
        out = ui_schema.migrate_legacy_profile(self.schema, legacy_profile())
        names = [t["name"] for t in out["tasks"]]
        self.assertEqual(names, ["Shop", "AutoProduce", "WeeklyProduce", "Business"])
        auto, weekly = out["tasks"][1], out["tasks"][2]
        self.assertFalse(auto["enabled"])
        self.assertNotIn("Produce目标", auto["option"])                       # -> default True End
        self.assertEqual((auto["option"]["Produce休息阈值"], auto["option"]["ProduceP道具"]), ("20%", "不带"))
        self.assertEqual(weekly, {"name": "WeeklyProduce", "enabled": True, "option": {}})
        self.assertEqual(out["removed_tasks"], [])

    def test_unchecked_or_removed_entries_stay_so(self):
        out = ui_schema.migrate_legacy_profile(self.schema, legacy_profile(enabled=False))
        self.assertFalse(out["tasks"][2]["enabled"])
        out = ui_schema.migrate_legacy_profile(self.schema, legacy_profile(removed=["AutoProduce"]))
        self.assertEqual(out["removed_tasks"], ["AutoProduce", "WeeklyProduce"])

    def test_migrated_profile_runs_and_never_starts_weekly_input(self):
        config = ui_schema.profile_to_run_config(self.schema, legacy_profile())
        by_name = {t["name"]: t for t in config["tasks"]}
        self.assertFalse(by_name["AutoProduce"]["enabled"])
        self.assertTrue(by_name["WeeklyProduce"]["enabled"])
        # Shop's default plus the weekly Auto start: since 2026-11-21 ticking WeeklyProduce is the stamina consent
        self.assertEqual(config.get("allow_spend"), ["shop-exchange-confirm", "weekly-auto-start"])
        plans = run_tasks.plan_tasks(run_tasks.load_interface(), config)
        weekly = next(p for p in plans if p.name == "WeeklyProduce")
        self.assertEqual(weekly.entry, "SC.WeeklyProduce.Entry")              # official Auto; every click disabled

    def test_current_profiles_are_unchanged(self):
        current = {"schema_version": 1, "preset": None, "removed_tasks": [], "tasks": [
            {"name": "AutoProduce", "enabled": True, "option": {"Produce目标": "True End", "Produce休息阈值": "10%"}}]}
        self.assertEqual(ui_schema.migrate_legacy_profile(self.schema, current), current)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class MigrationJsTests(unittest.TestCase):
    def test_js_matches_python_and_sanitize_keeps_the_result(self):
        cases = {"on": legacy_profile(), "off": legacy_profile(enabled=False),
                 "removed": legacy_profile(removed=["AutoProduce"])}
        script = ("import { migrateLegacyProfile, sanitizeProfile } from './ui/components/store.js';"
                  "import { readFileSync } from 'node:fs';"
                  "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
                  f"const cases = {json.dumps(cases, ensure_ascii=False)};"
                  "const out = {};"
                  "for (const [k, p] of Object.entries(cases)) { const s = sanitizeProfile(schema, p, null);"
                  " out[k] = { migrated: migrateLegacyProfile(schema, p),"
                  "  sanitized: s.tasks.filter((t) => ['AutoProduce', 'WeeklyProduce'].includes(t.name))"
                  "   .map((t) => [t.name, t.enabled, t.option['Produce目标'] ?? null, t.option['Produce休息阈值'] ?? null]),"
                  "  removed: s.removed_tasks }; }"
                  "console.log(JSON.stringify(out));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        schema = ui_schema.export_schema()
        for key, profile in cases.items():
            with self.subTest(key):
                self.assertEqual(out[key]["migrated"], ui_schema.migrate_legacy_profile(schema, profile))
        self.assertEqual(out["on"]["sanitized"], [["AutoProduce", False, "True End", "20%"],
                                                  ["WeeklyProduce", True, None, None]])
        self.assertEqual(out["off"]["sanitized"][1], ["WeeklyProduce", False, None, None])
        self.assertIn("WeeklyProduce", out["removed"]["removed"])


if __name__ == "__main__":
    unittest.main()
