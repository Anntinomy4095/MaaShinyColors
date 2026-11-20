"""「添加任务」: 方案 membership via profile ``removed_tasks``.

Covers the Python run-config mirror (removed tasks never run or consent to a spend), the JS store helpers (one
instance per task, add / remove / undo, legacy profiles), the picker rows (schema tasks only, developer visibility,
Match and Grade addable together) and the protocol / host sanitiser bounds. Offline: no host, no game, no input.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_tasks, ui_schema  # noqa: E402

UI = ROOT / "ui"


def run_node(script, *args):
    result = subprocess.run(["node", "--input-type=module", "-e", script, *args], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class RemovedTasksRunConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = ui_schema.export_schema()
        cls.interface = run_tasks.load_interface()

    def test_removed_task_is_disabled_and_consents_to_nothing(self):
        profile = {"schema_version": 1, "removed_tasks": ["Shop"], "tasks": [
            {"name": "Startup", "enabled": True}, {"name": "Shop", "enabled": True}, {"name": "Training", "enabled": True}]}
        config = ui_schema.profile_to_run_config(self.schema, profile)
        self.assertEqual({t["name"]: t["enabled"] for t in config["tasks"]}, {"Startup": True, "Shop": False, "Training": True})
        self.assertEqual(config["allow_spend"], ["training-start"])
        self.assertEqual(ui_schema.spend_flags_for_profile(self.schema, profile), ["allow_training_spend"])
        self.assertEqual([p.name for p in run_tasks.plan_tasks(self.interface, config)], ["Startup", "Training"])

    def test_invalid_removed_tasks_are_refused(self):
        tasks = [{"name": "Shop", "enabled": True}]
        for removed in (["Nope"], ["Shop", "Shop"], "Shop", [1], None):
            with self.assertRaises(ValueError, msg=removed):
                ui_schema.profile_to_run_config(self.schema, {"schema_version": 1, "removed_tasks": removed, "tasks": tasks})
        # A profile without the field (legacy) is unchanged.
        self.assertTrue(ui_schema.profile_to_run_config(self.schema, {"schema_version": 1, "tasks": tasks})["tasks"][0]["enabled"])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class StoreMembershipJsTests(unittest.TestCase):
    PRELUDE = ("import { readFileSync } from 'node:fs';"
               "import * as S from './ui/components/store.js';"
               "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));")

    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((UI / "fixtures" / "schema.json").read_text(encoding="utf-8"))
        cls.daily = next(p for p in cls.schema["presets"] if p["name"] == "daily")

    def test_preset_starts_with_only_its_tasks_in_the_plan(self):
        out = run_node(self.PRELUDE + "console.log(JSON.stringify([S.profileFromPreset(schema, 'daily'), S.profileFromPreset(schema, null)]));")
        daily, custom = out
        listed = [t["name"] for t in self.daily["task"]]
        self.assertEqual(daily["removed_tasks"], [t["name"] for t in self.schema["tasks"] if t["name"] not in listed])
        self.assertTrue(all(not t["enabled"] for t in daily["tasks"] if t["name"] in daily["removed_tasks"]))
        self.assertEqual(custom["removed_tasks"], [])

    def test_sanitize_keeps_legacy_profiles_whole_and_parks_new_schema_tasks(self):
        script = self.PRELUDE + (
            "const legacy = S.sanitizeProfile(schema, { schema_version: 1, preset: 'daily', tasks: [{ name: 'Business', enabled: true }] }, 'daily');"
            "const stored = { schema_version: 1, preset: 'daily', removed_tasks: ['Evil', 'Shop'],"
            "  tasks: [{ name: 'Shop', enabled: true }, { name: 'Startup', enabled: true }] };"
            "console.log(JSON.stringify([legacy, S.sanitizeProfile(schema, stored, 'daily')]));")
        legacy, current = run_node(script)
        self.assertEqual(legacy["removed_tasks"], [])
        self.assertTrue(next(t for t in legacy["tasks"] if t["name"] == "Business")["enabled"])
        names = [t["name"] for t in self.schema["tasks"]]
        self.assertEqual(sorted(current["removed_tasks"]), sorted(n for n in names if n != "Startup"))
        self.assertEqual([t["name"] for t in current["tasks"]][:2], ["Shop", "Startup"])
        self.assertFalse(next(t for t in current["tasks"] if t["name"] == "Shop")["enabled"])
        self.assertEqual(len(current["tasks"]), len(names))

    def test_add_remove_undo_keep_one_instance_and_exact_restore(self):
        # 2026-10-11: Business joined the daily preset, so the task added here is one outside it (Diagnostics).
        script = self.PRELUDE + (
            "const base = S.profileFromPreset(schema, 'daily');"
            "base.tasks.find((t) => t.name === 'Shop').option['商店兑换'] = 'No';"
            "const added = S.addTaskToPlan(schema, base, 'Diagnostics');"
            "const again = S.addTaskToPlan(schema, added, 'Diagnostics');"
            "const unknown = S.addTaskToPlan(schema, added, 'Evil');"
            "const { profile: removed, undo } = S.removeTaskFromPlan(added, 'Shop');"
            "const twice = S.removeTaskFromPlan(removed, 'Shop');"
            "const restored = S.restoreTaskToPlan(removed, undo);"
            "console.log(JSON.stringify({ base, added, sameAgain: again === added, sameUnknown: unknown === added, removed, undo,"
            "  twiceUndo: twice.undo, restored, addable: S.addableTasks(schema, added),"
            "  config: S.profileToRunConfig(schema, removed), flags: S.profileSpendFlags(schema, removed) }));")
        out = run_node(script)
        added = out["added"]
        self.assertEqual(added["tasks"][-1], {"name": "Diagnostics", "enabled": True, "option": added["tasks"][-1]["option"]})
        self.assertNotIn("Diagnostics", added["removed_tasks"])
        self.assertTrue(out["sameAgain"] and out["sameUnknown"])
        for profile in (added, out["removed"], out["restored"]):
            names = [t["name"] for t in profile["tasks"]]
            self.assertEqual(len(names), len(set(names)))  # run configs reject a repeated task name
        self.assertIn("Shop", out["removed"]["removed_tasks"])
        self.assertFalse(next(t for t in out["removed"]["tasks"] if t["name"] == "Shop")["enabled"])
        self.assertIsNone(out["twiceUndo"])
        self.assertEqual(out["restored"]["tasks"], added["tasks"])  # position, checkbox and options come back
        self.assertEqual(out["restored"]["removed_tasks"], added["removed_tasks"])
        self.assertNotIn("Diagnostics", out["addable"])
        # JS mirrors Python for the removed profile (spend flags travel separately in run.start).
        python = ui_schema.profile_to_run_config(self.schema, {k: out["removed"][k] for k in ("schema_version", "tasks", "removed_tasks")})
        self.assertEqual(out["config"]["tasks"], python["tasks"])
        self.assertEqual(out["flags"], ui_schema.spend_flags_for_profile(self.schema, out["removed"]))
        self.assertNotIn("allow_shop_spend", out["flags"])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PickerRowsJsTests(unittest.TestCase):
    def rows(self, developer, enabled_match=True, running=False):
        script = (
            "import { readFileSync } from 'node:fs';"
            "import { profileFromPreset } from './ui/components/store.js';"
            "import { pickerRows, rowMenuItems } from './ui/components/task-add.js';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "const [developer, match, running] = JSON.parse(process.argv[1]);"
            "const profile = profileFromPreset(schema, 'daily');"
            "profile.tasks.find((t) => t.name === 'MatchSkip').enabled = match;"
            "profile.removed_tasks.push('GradeFes');"
            "const state = { schema, annotations, profile, prefs: { developer }, run: { active: running }, expanded: [] };"
            "const menu = rowMenuItems(state, {}, { name: 'Shop', index: 1, prev: null, next: 2 })"
            "  .map((m) => ({ label: m.label, disabled: Boolean(m.disabled), danger: Boolean(m.danger) }));"
            "console.log(JSON.stringify({ rows: pickerRows(state), menu }));")
        return run_node(script, json.dumps([developer, enabled_match, running]))

    def test_only_schema_tasks_with_developer_visibility(self):
        annotations = json.loads((UI / "annotations.json").read_text(encoding="utf-8"))
        schema_names = {t["name"] for t in json.loads((UI / "fixtures" / "schema.json").read_text(encoding="utf-8"))["tasks"]}
        dev_tasks = {n for n, note in annotations["tasks"].items() if note.get("dev")}
        user = self.rows(False)["rows"]
        dev = self.rows(True)["rows"]
        self.assertTrue({r["name"] for r in dev} <= schema_names)
        # 2026-10-13: only daily-queue tasks: W.I.N.G. has its 「Produce」 tab, 每日任务进度 runs automatically;
        # 2026-10-26: 只读取资源 starts from 主页 → 更新资源
        self.assertEqual({r["name"] for r in dev}, schema_names - {"AutoProduce", "MissionStatus", "ResourceRead"})
        self.assertFalse({r["name"] for r in user} & dev_tasks)
        self.assertTrue(all(r["dev"] for r in dev if r["name"] in dev_tasks))
        # Addable first, then 已添加; every row has a short description.
        added = [r["added"] for r in user]
        self.assertEqual(added, sorted(added))
        self.assertIn("Business", [r["name"] for r in user if r["added"]])        # 2026-10-11: in the daily preset
        self.assertNotIn("AutoProduce", [r["name"] for r in user])
        self.assertNotIn("MissionStatus", [r["name"] for r in user])
        # 2026-10-29: WeeklyProduce (official Auto, start enabled) is offered like the other daily tasks
        self.assertIn("WeeklyProduce", [r["name"] for r in user])
        self.assertTrue(all(r["blurb"] and len(r["blurb"]) <= 20 for r in dev))

    def test_match_and_grade_may_be_added_together(self):
        # 2026-10-07: Match and Grade share フェス参加権 on one day; the split is planned (fes-plan.js), not excluded.
        grade = next(r for r in self.rows(False)["rows"] if r["name"] == "GradeFes")
        self.assertFalse(grade["added"])
        self.assertIsNone(grade["conflict"])
        free = next(r for r in self.rows(False, enabled_match=False)["rows"] if r["name"] == "GradeFes")
        self.assertIsNone(free["conflict"])

    def test_row_menu_disables_moves_at_edges_and_everything_while_running(self):
        idle = {m["label"]: m for m in self.rows(False)["menu"]}
        self.assertTrue(idle["上移"]["disabled"])
        self.assertFalse(idle["下移"]["disabled"])
        self.assertFalse(idle["移出方案"]["disabled"])
        self.assertTrue(idle["移出方案"]["danger"])
        busy = {m["label"]: m for m in self.rows(False, running=True)["menu"]}
        self.assertTrue(busy["移出方案"]["disabled"] and busy["下移"]["disabled"])
        # 2026-11-23: 只勾选此项 picks a few tasks without 全取消 first
        self.assertFalse(idle["只勾选此项"]["disabled"])
        self.assertTrue(busy["只勾选此项"]["disabled"])

    def test_set_only_ticks_one_shown_row(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn("setOnly: (name) =>", app)
        self.assertIn("t.enabled = t.name === name", app)


class ProtocolAndHostTests(unittest.TestCase):
    def test_protocol_profile_field_matches_host_bound(self):
        protocol = json.loads((UI / "protocol.json").read_text(encoding="utf-8"))
        field = protocol["definitions"]["profile"]["properties"]["removed_tasks"]
        self.assertEqual(field["type"], "array")
        self.assertTrue(field["uniqueItems"])
        store = (ROOT / "browser_host" / "Ui" / "UiProfileStore.cs").read_text(encoding="utf-8")
        bound = re.search(r"MaxRemovedTasks = (\d+);", store)
        self.assertIsNotNone(bound)
        self.assertEqual(int(bound.group(1)), field["maxItems"])
        self.assertIn('source["removed_tasks"]', store)
        self.assertIn("!removed.Contains(name)", store)  # removed tasks are saved disabled

    def test_page_persists_the_field_and_adds_only_through_actions(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn("removed_tasks: s.profile.removed_tasks || []", app)
        add = (UI / "components" / "task-add.js").read_text(encoding="utf-8")
        self.assertNotIn("bridge", add)  # the picker never talks to the host itself


if __name__ == "__main__":
    unittest.main()
