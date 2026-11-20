"""UI overhaul U0/U0.2: tools/ui_schema.py export, committed snapshots, the UI profile -> run config round trip and
the U0.2 rule that spend flags come only from each task's explicit spend option."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from tools import run_tasks, ui_schema
from tools.check_project import load_jsonc

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "fixtures" / "ui" / "schema_snapshot.json"
PROFILE = ROOT / "tests" / "fixtures" / "ui" / "profile.json"
UI = ROOT / "ui"


class UiSchemaExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = ui_schema.export_schema()
        cls.interface = run_tasks.load_interface()

    def test_snapshot_and_preview_fixture_match_the_live_export(self):
        text = ui_schema.dumps(self.schema)
        self.assertEqual(SNAPSHOT.read_text(encoding="utf-8"), text,
                         "重新生成：python tools/ui_schema.py > tests/fixtures/ui/schema_snapshot.json")
        self.assertEqual((UI / "fixtures" / "schema.json").read_text(encoding="utf-8"), text,
                         "重新生成：python tools/ui_schema.py > ui/fixtures/schema.json")

    def test_generated_modules_are_in_sync(self):
        self.assertEqual((UI / "fixtures" / "mock-bundle.js").read_text(encoding="utf-8"), ui_schema.mock_bundle())
        self.assertEqual((UI / "protocol.js").read_text(encoding="utf-8"), ui_schema.protocol_module())

    def test_all_tasks_and_options_parse(self):
        self.assertEqual([t["name"] for t in self.schema["tasks"]], list(self.interface.tasks))
        self.assertEqual(len(self.schema["tasks"]), 16)  # +CollaboFes; +PresentClaim/WeeklyCheck, +WeeklyProduce (10-08), +ResourceRead (10-26)
        interface_options = {k for k, o in self.schema["options"].items() if not o.get("ui_only")}
        self.assertEqual(interface_options, set(self.interface.options))
        # +12 Produce (2026-10-05 编队), +14 W.I.N.G. T.E., +1 プレゼント,
        # +4 Fes 参加权分配 (2026-10-07: Match回复券上限, Grade彩排次数, Grade回复券, Grade回复券上限), +1 训练列表滚动 (10-08)
        # +5 W.I.N.G. end/SP options; +Grade参加ユニット, 营业一括派出, 营业队伍 (batch 4, 10-08); +Produce找编队 (10-11)
        # +营业收取后 (10-11: 再営業へ → 決定 / 只收取; already counted in 88 together with Produce找编队)
        self.assertEqual(len(interface_options), 117)  # +1 Collabo满员时; +4 Collabo会场/局数/编队/等级; +3 Produce剧本/Halo路线/Halo带书, -1 Halo路线 (11-07); +3 Match推进/Match编队/Match失败重试; +2 Autoループ/Autoソーダ上限; −1 Auto核对ユニット名 (2026-10-30, Maa sets オート1); +1 Auto编队来源; −1 Auto设定オート1 (2026-10-30, always set); +3 Auto带书/育成方針/核对ユニット名 −1 Auto核对P偶像; +2 Auto设定オート1/Auto编队; +4 Produce限定试镜S1–S4; +visible Shop coin reserve; +Produce大富豪; +4 WeeklyProduce; +Produce改游戏设置; +3 新书锁定/卖书
        ui_only = {k for k, o in self.schema["options"].items() if o.get("ui_only")}
        self.assertEqual(ui_only, set(ui_schema.UI_ONLY_OPTIONS))
        self.assertFalse(ui_only & set(self.interface.options))
        for task in self.schema["tasks"]:
            self.assertTrue(task["label"] and task["entry"].startswith("SC."), task["name"])
            for key in task["options"]:
                self.assertIn(key, self.schema["options"], f"{task['name']} -> {key}")
            self.assertEqual(set(task["ui"]), {"spend_flag", "spend_action", "spend_option", "migrated"})

    def test_every_option_has_known_type_and_existing_default_case(self):
        for key, option in self.schema["options"].items():
            self.assertIn(option["type"], ("select", "switch", "input"), key)
            names = [case["name"] for case in option["cases"]]
            if option["type"] == "input":  # single-input option: default is the input's default string
                self.assertEqual(len(option["inputs"]), 1, key)
                self.assertEqual(option["default_case"], option["inputs"][0].get("default", ""), key)
                continue
            self.assertIn(option["default_case"], names, key)
            self.assertEqual(len(names), len(set(names)), key)
            if option["type"] == "switch":
                # 2026-10-13: the retired 开局 choice keeps only Yes (saved "No" values are migrated away)
                self.assertEqual(set(names), {"Yes"} if key in ("Produce可开新局", "Collabo使用Pass", "Auto开始消耗体力") else {"Yes", "No"}, key)

    def test_cases_carry_no_pipeline_override(self):
        self.assertNotIn("pipeline_override", json.dumps(self.schema, ensure_ascii=False))

    def test_daily_preset_order_matches_presets_file(self):
        daily = load_jsonc(ROOT / "assets" / "presets" / "daily.json")["preset"][0]
        exported = next(p for p in self.schema["presets"] if p["name"] == "daily")
        self.assertEqual([t["name"] for t in exported["task"]], [t["name"] for t in daily["task"]])
        self.assertEqual(exported["task"], daily["task"])

    def test_spend_flags_match_run_tasks(self):
        self.assertEqual(self.schema["run_config"]["spend_flags"], run_tasks.SPEND_FLAGS)
        by_task = {t["name"]: t["ui"] for t in self.schema["tasks"]}
        expected = {"Shop": "allow_shop_spend", "Training": "allow_training_spend",
                    "MatchSkip": "allow_match_spend", "GradeFes": "allow_grade_spend",
                    # 2026-10-11: the W.I.N.G. settings run through the supervised Wing entry (tools/ui_wing.py)
                    "AutoProduce": "allow_produce_spend",
                    "WeeklyProduce": "allow_weekly_auto_spend",   # 2026-10-29: official Auto プロデュース開始
                    "CollaboFes": "allow_collabo_spend"}          # 2026-11-18: テンション決定 (コラボフェスパス 1)
        self.assertEqual({n: u["spend_flag"] for n, u in by_task.items() if u["spend_flag"]}, expected)
        for name, flag in expected.items():
            self.assertEqual(by_task[name]["spend_action"], run_tasks.SPEND_FLAGS[flag])
        for name, ui in by_task.items():
            self.assertNotIn("click_cap", ui, name)   # no click caps, none shown

    def test_spend_options_bind_each_spend_task(self):
        by_task = {t["name"]: t for t in self.schema["tasks"]}
        for name, task in by_task.items():
            spend = task["ui"]["spend_option"]
            self.assertEqual(bool(spend), bool(task["ui"]["spend_flag"]), name)
            if not spend:
                continue
            self.assertIn(spend["key"], task["options"])
            option = self.schema["options"][spend["key"]]
            self.assertEqual(option["type"], "switch")
            self.assertIn(spend["when"], {"Yes", "No"})
        # Defaults: ordinary approved daily consumption on; Match skips (只采样 defaults to No since 2026-10-11: the user's
        # default is to use the remaining rights); Grade rights off until the user configures and opens them.
        consenting_by_default = {n for n, t in by_task.items() if t["ui"]["spend_option"]
                                 and self.schema["options"][t["ui"]["spend_option"]["key"]]["default_case"] == t["ui"]["spend_option"]["when"]}
        self.assertEqual(consenting_by_default, {"Shop", "Training", "MatchSkip", "AutoProduce", "CollaboFes", "WeeklyProduce"})  # Collabo: 11-18 「默认用完」; WeeklyProduce: 11-21 勾选即同意
        self.assertEqual(by_task["GradeFes"]["ui"]["spend_option"], {"key": "Grade使用参加权", "when": "Yes"})
        self.assertEqual(by_task["MatchSkip"]["ui"]["spend_option"], {"key": "Match只采样", "when": "No"})
        # 2026-10-12 (user): one run = one W.I.N.G.; a new Produce (stamina 30) is allowed by default and the host's
        # per-run confirmation is the consent (at most one Produce per run). The old 「Produce开新一局」 key is dropped.
        self.assertEqual(by_task["AutoProduce"]["ui"]["spend_option"], {"key": "Produce可开新局", "when": "Yes"})
        self.assertEqual(self.schema["options"]["Produce可开新局"]["default_case"], "Yes")
        self.assertIn("Produce开新一局", ui_schema.REMOVED_OPTIONS["AutoProduce"])

    def test_annotations_display_layer(self):
        annotations = json.loads((UI / "annotations.json").read_text(encoding="utf-8"))
        self.assertEqual(annotations["annotations_version"], 2)
        for name, note in annotations["tasks"].items():
            self.assertTrue(note.get("name"), name)
            for mark in ("（", "(", "只读", "未迁移", "采样"):
                self.assertNotIn(mark, note["name"], name)
            if note.get("item"):
                self.assertIn(note["item"], annotations["items"])
        dev = {n for n, a in annotations["tasks"].items() if a.get("dev")}
        # 2026-10-11: 礼物箱 / 任务奖励 run by default, so they are visible (no longer developer-only)
        self.assertEqual(dev, {"Diagnostics", "ProduceDryRun", "WeeklyCheck"})  # Produce: settings visible, run unmigrated
        for key, note in annotations["options"].items():
            self.assertIn(key, self.schema["options"], key)
            names = {c["name"] for c in self.schema["options"][key]["cases"]}
            self.assertLessEqual(set(note.get("cases", {})), names, key)
            self.assertLessEqual(set(note.get("dev_cases", [])), names, key)
            self.assertLessEqual(len(note["label"]), 10, key)
            if "under" in note:  # display-only nesting: parent must be a Yes/No switch in the same task
                parent = self.schema["options"][note["under"]]
                self.assertEqual({c["name"] for c in parent["cases"]}, {"Yes", "No"}, key)
                owners = [t for t in self.schema["tasks"] if key in t.get("options", [])]
                self.assertTrue(owners and all(note["under"] in t["options"] for t in owners), key)
        for key, note in annotations["options"].items():
            if note.get("control") != "item_picker":
                continue  # multi-select picker members: Yes/No switches of one task, grouped by a declared group
            self.assertEqual({c["name"] for c in self.schema["options"][key]["cases"]}, {"Yes", "No"}, key)
            self.assertIn(note["group"], annotations["option_groups"], key)
            self.assertIn(note["item"], annotations["items"], key)
        card_options = [k for k, n in annotations["options"].items() if n.get("control") == "card_picker"]
        self.assertEqual(card_options, ["训练卡"])
        self.assertNotIn("cards", annotations)   # 2026-10-10: the picker reads names from the card manifest, not per-case notes

    def test_shop_picker_matches_the_catalog(self):
        from agent.core import shop_task as st
        annotations = json.loads((UI / "annotations.json").read_text(encoding="utf-8"))
        shop = next(t for t in self.schema["tasks"] if t["name"] == "Shop")
        picker = {k: n for k, n in annotations["options"].items() if n.get("group") == "商店其他商品"}
        expected = {f"商店其他·{row[0]}@{row[2]}": (i, row) for i, row in st.SHOP_ITEMS.items() if i not in st.MAIN_ITEMS}
        self.assertEqual(set(picker), set(expected))
        self.assertEqual([k for k in shop["options"] if k in picker], list(picker))  # sections stay contiguous
        for key, (item_id, (ja, zh, price, discount, kind, days)) in expected.items():
            note = picker[key]
            self.assertEqual((note["label"], note["price"], note.get("discount", ""), note["days"]),
                             (zh, price, discount, list(days)), key)
            self.assertEqual(self.schema["options"][key]["default_case"],
                             "Yes" if item_id in st.DEFAULT_SELECTED else "No", key)
        rule = annotations["tasks"]["Shop"]["require_any"]
        self.assertEqual(rule["options"], [k for k in shop["options"] if k not in ("商店预算", "商店兑换", "商店余额保留")])
        self.assertIn("マニーショップで買い物しよう", rule["message"])
        for gone in ("商店必购跳过券", "商店其他商品", "商店其他商品单价上限", "商店跳过券单价上限"):
            self.assertNotIn(gone, annotations["options"])
            self.assertNotIn(gone, self.schema["options"])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_shop_picker_rows_summary_and_warning(self):
        script = ("import { optionRows, requireAnyWarning, visibleOptions } from './ui/components/display.js';"
                  "import { groupSummary, pickerItems } from './ui/components/card-picker.js';"
                  "import { readFileSync } from 'node:fs';"
                  "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
                  "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
                  "const state = { schema, annotations, prefs: {} };"
                  "const task = schema.tasks.find((t) => t.name === 'Shop');"
                  "const defaults = Object.fromEntries(task.options.map((k) => [k, schema.options[k].default_case]));"
                  "const off = Object.fromEntries(task.options.map((k) => [k, k === '商店预算' ? defaults[k] : 'No']));"
                  "const rows = optionRows(state, task, { enabled: true, option: defaults });"
                  "const group = rows.find((r) => r.group);"
                  "const one = { ...off, '商店其他·シルバートレーニングチケット@283': 'Yes' };"
                  "const items = pickerItems(state, group.keys);"
                  "console.log(JSON.stringify({ rows: rows.map((r) => r.key || ('group:' + r.group)), members: group.keys.length,"
                  " visible: visibleOptions(state, task, { enabled: true, option: defaults }).length,"
                  " summary: [groupSummary(state, group.group, group.keys, defaults), groupSummary(state, group.group, group.keys, one),"
                  "   groupSummary(state, group.group, group.keys, off)],"
                  " warn: [requireAnyWarning(state, task, { enabled: true, option: defaults }), requireAnyWarning(state, task, { enabled: true, option: off }),"
                  "   requireAnyWarning(state, task, { enabled: false, option: off }), requireAnyWarning(state, task, { enabled: true, option: { ...off, '商店跳过券': 'Yes' } })],"
                  " first: [items[0].name, items[0].sub, items[0].sub2, items[0].badge, items[0].section], sections: [...new Set(items.map((i) => i.section))] }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["rows"], ["商店跳过券", "商店金训练券", "商店余额保留", "group:商店其他商品"])
        self.assertEqual(out["members"], 25)
        self.assertEqual(out["visible"], 28)
        self.assertEqual(out["summary"], ["未选", "银训练券", "未选"])  # 2026-10-07: third offers default off
        self.assertEqual(out["warn"], [None, "未选商品：每日任务「マニーショップで買い物しよう」无法完成", None, None])
        # 2026-10-11: tiles show the game's own name; the short Chinese label stays for the summary row
        self.assertEqual(out["first"], ["ゴールドトレーニングチケット", "283 マニー", "每月 9/14/25 日", "71%", "训练券"])
        self.assertEqual(out["sections"], ["训练券", "道具", "毛巾"])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_roleplay_warnings_and_business_team_field(self):
        """Roleplay 2026-10-10: a ticked Grade Fes with its inner switch off, and a 派出 without a
        team field were silent dead ends; the team input must appear only when 派出 is chosen.
        Roleplay 2026-10-26 (user: 勾选即同意): the Grade tick is the consent, so the inner switch follows it and is hidden."""
        script = ("import { requireAnyWarning, visibleOptions, consentOptions } from './ui/components/display.js';"
                  "import { applyConsent } from './ui/components/store.js';"
                  "import { readFileSync } from 'node:fs';"
                  "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
                  "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
                  "const state = { schema, annotations, prefs: {} };"
                  "const t = (n) => schema.tasks.find((x) => x.name === n);"
                  "const dispatch = '核对后派出（需填写队伍）';"
                  "console.log(JSON.stringify({"
                  " train: [requireAnyWarning(state, t('Training'), { enabled: true, option: {} }),"
                  "   requireAnyWarning(state, t('Training'), { enabled: true, option: { '训练卡': '2050170010' } }),"
                  "   requireAnyWarning(state, t('Training'), { enabled: false, option: {} })],"
                  " grade: [requireAnyWarning(state, t('GradeFes'), { enabled: true, option: {} }),"
                  "   requireAnyWarning(state, t('GradeFes'), { enabled: true, option: { 'Grade使用参加权': 'Yes' } })],"
                  " gradeVisible: visibleOptions(state, t('GradeFes'), { enabled: true, option: {} }).includes('Grade使用参加权'),"
                  " consent: consentOptions(annotations),"
                  " ticked: applyConsent({ tasks: [{ name: 'GradeFes', enabled: true, option: {} }] }, consentOptions(annotations)).tasks[0],"
                  " unticked: applyConsent({ tasks: [{ name: 'GradeFes', enabled: false, option: { 'Grade使用参加权': 'Yes' } }] }, consentOptions(annotations)).tasks[0],"
                  " loaded: applyConsent({ tasks: [{ name: 'GradeFes', enabled: true, option: {} }] }, consentOptions(annotations), { load: true }).tasks[0],"
                  " teamOff: visibleOptions(state, t('Business'), { enabled: true, option: {} }).includes('营业队伍'),"
                  " teamOn: visibleOptions(state, t('Business'), { enabled: true, option: { '营业一括派出': dispatch } }).includes('营业队伍'),"
                  " devOn: visibleOptions({ ...state, prefs: { developer: true } }, t('Business'), { enabled: true, option: { '营业一括派出': dispatch } }).includes('营业队伍') }));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["train"], [None, None, None])   # 2026-10-10: no S card = default mode, not a dead end
        self.assertEqual(out["grade"], [None, None])   # no dead end left: the tick sets the switch
        self.assertFalse(out["gradeVisible"])
        self.assertEqual(out["consent"], {"GradeFes": "Grade使用参加权"})
        self.assertEqual((out["ticked"]["enabled"], out["ticked"]["option"]["Grade使用参加权"]), (True, "Yes"))
        self.assertEqual(out["unticked"]["option"]["Grade使用参加权"], "No")
        # a saved profile ticked without the consent never spent: it loads unticked, never switched on
        self.assertEqual((out["loaded"]["enabled"], out["loaded"]["option"]["Grade使用参加权"]), (False, "No"))
        # 2026-10-11: the default 再营业 uses the game's prefilled team (checked on the select page): no team field for
        # users; the old 一括営業 team input stays a developer option.
        self.assertFalse(out["teamOff"])
        self.assertFalse(out["teamOn"])
        self.assertTrue(out["devOn"])

    def test_unmigrated_flags_match_annotations(self):
        annotations = json.loads((UI / "annotations.json").read_text(encoding="utf-8"))
        unmigrated = {t["name"] for t in self.schema["tasks"] if not t["ui"]["migrated"]}
        # PresentClaim: dropped the 未迁移 mark (real flow, clicks/actions still disabled, dev-only)
        self.assertEqual(unmigrated, set())  # 2026-10-29: WeeklyProduce migrated (official Auto start enabled)
        self.assertEqual({n for n, a in annotations["tasks"].items() if a.get("migrated") is False}, unmigrated)
        self.assertEqual(set(annotations["tasks"]), {t["name"] for t in self.schema["tasks"]})
        for name, note in annotations["tasks"].items():
            for key in (note.get("spend") or {}).get("budget_options", []):
                self.assertIn(key, self.schema["options"], f"{name} -> {key}")
        for name, note in annotations["tasks"].items():
            for key in (note.get("require_any") or {}).get("options", []):
                self.assertIn(key, next(t for t in self.schema["tasks"] if t["name"] == name)["options"], f"{name} -> {key}")
                self.assertEqual({c["name"] for c in self.schema["options"][key]["cases"]}, {"Yes", "No"}, key)
        for rule in annotations["mutual_exclusion"]:
            self.assertTrue(set(rule["tasks"]) <= set(annotations["tasks"]))
        spend_tasks = {t["name"] for t in self.schema["tasks"] if t["ui"]["spend_flag"]}
        self.assertEqual({n for n, a in annotations["tasks"].items() if a.get("spend")}, spend_tasks)


class UiProfileRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = ui_schema.export_schema()
        cls.interface = run_tasks.load_interface()

    def test_profile_becomes_a_plannable_run_config_without_authorization(self):
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        config = ui_schema.profile_to_run_config(self.schema, profile)
        self.assertLessEqual(set(config), run_tasks.RUN_CONFIG_KEYS)
        for forbidden in ("input_enabled", "families", "max_clicks", "task_max_clicks"):
            self.assertNotIn(forbidden, config)
        # Shop is enabled and its spend option defaults on; Grade is enabled but 使用 Fes 参加权 is off; Match disabled.
        self.assertEqual(config["allow_spend"], ["shop-exchange-confirm"])
        self.assertEqual(ui_schema.spend_flags_for_profile(self.schema, profile), ["allow_shop_spend"])
        plans = run_tasks.plan_tasks(self.interface, config)
        self.assertEqual([p.name for p in plans], ["Startup", "MissionStatus", "Shop", "GradeFes"])
        shop = next(p for p in plans if p.name == "Shop")
        self.assertEqual(shop.pipeline_override["SC.Shop.Config.SkipTicket"]["attach"], {"buy_skip_ticket": True})
        self.assertEqual(shop.pipeline_override["SC.Shop.Config.GoldTrainingTicket"]["attach"],
                         {"buy_gold_training_ticket": False})
        self.assertEqual(shop.pipeline_override["SC.Shop.Config.Other.SilverTrainingTicket283"]["attach"],
                         {"buy_silver_training_ticket_283": False})
        grade = next(p for p in plans if p.name == "GradeFes")
        self.assertEqual(grade.pipeline_override["SC.Grade.Config.Difficulty"]["attach"]["difficulty"], "Hard")
        self.assertEqual(config["task_timeout_s"], 600)

    def test_stale_option_case_never_fails_the_run_config(self):
        # 2026-10-28 live: 「未运行：选项 プレゼント领取 没有分支 开启（一括，每日 1 次）」.
        for value in ("开启（一括，每日 1 次）", "开启（一括，含未知物品）"):
            with self.subTest(value=value):
                profile = {"schema_version": 1, "tasks": [{"name": "PresentClaim", "option": {"プレゼント领取": value}}]}
                config = ui_schema.profile_to_run_config(self.schema, profile)
                self.assertEqual(config["tasks"][0]["option"], {"プレゼント领取": "开启"})
        gone = {"schema_version": 1, "tasks": [{"name": "PresentClaim", "option": {"プレゼント领取": "不存在的分支"}}]}
        config = ui_schema.profile_to_run_config(self.schema, gone)
        self.assertNotIn("option", config["tasks"][0])           # the option's default applies
        plan, = run_tasks.plan_tasks(self.interface, config)
        self.assertEqual(plan.pipeline_override["SC.Present.Config"]["attach"], {"enabled": True})

    def test_full_daily_profile_round_trip(self):
        preset = next(p for p in self.schema["presets"] if p["name"] == "daily")
        profile = {"schema_version": 1, "preset": "daily", "tasks": preset["task"]}
        config = ui_schema.profile_to_run_config(self.schema, profile)
        self.assertEqual([p.name for p in run_tasks.plan_tasks(self.interface, config)],
                         [t["name"] for t in preset["task"] if t.get("enabled", True)])

    def test_spend_flags_come_only_from_explicit_task_options(self):
        def run(tasks):
            profile = {"schema_version": 1, "tasks": tasks}
            config = ui_schema.profile_to_run_config(self.schema, profile)
            flags = ui_schema.spend_flags_for_profile(self.schema, profile)
            self.assertLessEqual(set(flags), set(run_tasks.SPEND_FLAGS))
            # Match 推进 adds ライブ開始 to the Skip's スキップ開始 under the same flag.
            self.assertEqual(config.get("allow_spend", []), sorted(
                a for f in flags for a in ([run_tasks.SPEND_FLAGS[f], run_tasks.MATCH_PLAY_SPEND_ACTION]
                                           if f == "allow_match_spend" and any(
                                               t.get("name") == "MatchSkip" and (t.get("option") or {}).get("Match推进")
                                               != "只跳过" for t in tasks) else [run_tasks.SPEND_FLAGS[f]])))
            self.assertNotIn("input_enabled", config)
            run_tasks.plan_tasks(self.interface, config)  # still a valid run config
            for item in config["tasks"]:
                for key in item.get("option", {}):
                    self.assertFalse(self.schema["options"][key].get("ui_only"), key)
            return flags

        self.assertEqual(run([{"name": "GradeFes", "enabled": True}]), [])
        self.assertEqual(run([{"name": "GradeFes", "enabled": True, "option": {"Grade使用参加权": "Yes"}}]), ["allow_grade_spend"])
        self.assertEqual(run([{"name": "GradeFes", "enabled": False, "option": {"Grade使用参加权": "Yes"}}]), [])
        self.assertEqual(run([{"name": "MatchSkip", "enabled": True}]), ["allow_match_spend"])   # 只采样 defaults to No
        self.assertEqual(run([{"name": "MatchSkip", "enabled": True, "option": {"Match只采样": "Yes"}}]), [])
        skip_only = {"schema_version": 1, "tasks": [{"name": "MatchSkip", "option": {"Match推进": "只跳过"}}]}
        self.assertEqual(ui_schema.profile_to_run_config(self.schema, skip_only)["allow_spend"], ["match-skip-confirm"])
        advance = {"schema_version": 1, "tasks": [{"name": "MatchSkip"}]}
        self.assertEqual(ui_schema.profile_to_run_config(self.schema, advance)["allow_spend"],
                         ["match-play-live-start", "match-skip-confirm"])
        self.assertEqual(run([{"name": "MatchSkip", "enabled": False}]), [])
        self.assertEqual(run([{"name": "Shop", "enabled": True, "option": {"商店兑换": "No"}},
                              {"name": "Training", "enabled": True, "option": {"训练使用铜券": "No"}}]), [])
        self.assertEqual(run([{"name": "Shop", "enabled": True}, {"name": "Training", "enabled": True}]),
                         ["allow_shop_spend", "allow_training_spend"])
        self.assertEqual(run([{"name": "Startup", "enabled": True}, {"name": "Business", "enabled": True},
                              {"name": "Diagnostics", "enabled": True}]), [])
        with self.assertRaises(ValueError):  # a spend switch of another task is not accepted
            ui_schema.profile_to_run_config(self.schema, {"schema_version": 1, "tasks": [
                {"name": "Shop", "option": {"Grade使用参加权": "Yes"}}]})

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_store_mirrors_python_spend_and_config(self):
        profiles = [
            json.loads(PROFILE.read_text(encoding="utf-8")),
            {"schema_version": 1, "tasks": [{"name": "GradeFes", "enabled": True, "option": {"Grade使用参加权": "Yes", "Grade难度": "Hard"}},
                                            {"name": "MatchSkip", "enabled": True, "option": {"Match只采样": "No"}},
                                            {"name": "Training", "enabled": True, "option": {"训练使用铜券": "No"}}]},
        ]
        script = ("import { profileToRunConfig, profileSpendFlags } from './ui/components/store.js';"
                  "import { readFileSync } from 'node:fs';"
                  "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
                  "const profiles = JSON.parse(process.argv[1]);"
                  "console.log(JSON.stringify(profiles.map((p) => ({ config: profileToRunConfig(schema, p), flags: profileSpendFlags(schema, p) }))));")
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(profiles, ensure_ascii=False)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        for profile, js in zip(profiles, json.loads(result.stdout)):
            config = ui_schema.profile_to_run_config(self.schema, profile)
            self.assertEqual(js["flags"], ui_schema.spend_flags_for_profile(self.schema, profile))
            python_tasks = [{**t, "enabled": t["enabled"]} for t in config["tasks"]]
            js_tasks = [t for t in js["config"]["tasks"] if t["name"] in {p["name"] for p in profile["tasks"]}]
            self.assertEqual(js_tasks, python_tasks)
            self.assertNotIn("allow_spend", js["config"])  # the UI sends spend flags separately (run.start)

    def test_rejects_unknown_fields_tasks_options_and_cases(self):
        good = {"schema_version": 1, "tasks": [{"name": "Shop", "enabled": True}]}
        bad = [
            {**good, "allow_spend": ["shop-exchange-confirm"]},
            {**good, "input_enabled": True},
            {"schema_version": 1, "tasks": [{"name": "Nope"}]},
            {"schema_version": 1, "tasks": [{"name": "Shop", "option": {"Match次数": "20"}}]},
            {"schema_version": 1, "tasks": [{"name": "Shop", "option": {"商店必购跳过券": "Maybe"}}]},
            {"schema_version": 1, "tasks": [{"name": "Shop", "spend": True}]},
            {"schema_version": 1, "tasks": [{"name": "Shop"}, {"name": "Shop"}]},
            {"schema_version": 1, "tasks": []},
        ]
        for profile in bad:
            with self.assertRaises(ValueError, msg=profile):
                ui_schema.profile_to_run_config(self.schema, profile)


class UiSchemaCliTests(unittest.TestCase):
    def test_out_is_limited_to_private_ui_dir(self):
        for raw in ("ui/fixtures/schema.json", "dev_assets/private/other.json", "dev_assets/private/ui",
                    "dev_assets/private/ui/../x.json", str(ROOT / "README.md")):
            with self.assertRaises(ValueError, msg=raw):
                ui_schema.safe_out_path(str(ROOT / raw) if not Path(raw).is_absolute() else raw)
        ok = ui_schema.safe_out_path(str(ROOT / "dev_assets" / "private" / "ui" / "schema.json"))
        self.assertEqual(ok.name, "schema.json")


if __name__ == "__main__":
    unittest.main()
