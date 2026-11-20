"""任务页设置精简（2026-10-09，用户反馈）：商店去掉每日上限、其他商品配游戏图标；训练只留 偶像 / 券 / 数量；Match 关卡四选
（最新 Vo/Da/Vi 道具关卡 + 自定义）、次数在关卡下；Grade 平铺、模式二选一、まとめて消費 1–5、次数到 20；计数用步进器；
旧档案迁移（UI store 与 tools/ui_schema 一致）。离线，无游戏输入。
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from agent.core import fes_allocation as fa
from agent.core import grade_task as gt
from agent.core import match_stage_target as mst
from agent.core import match_task as mt
from agent.core import training_task as tt
from agent.core.match_progress import MatchProgress
from tools import run_tasks, ui_schema
from tools.check_project import load_jsonc

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "assets" / "resource" / "base" / "pipeline"


def attach_of(interface, key, case):
    option = interface.options[key]
    body = next(c for c in option["cases"] if c["name"] == case)["pipeline_override"]
    merged = {}
    for node in body.values():
        merged.update(node["attach"])
    return merged


def entry_params(pipeline, entry, overrides=()):
    """Merged attach of a task's config nodes (pipeline defaults, then the chosen option overrides)."""
    nodes = pipeline[entry]["custom_action_param"]["config_nodes"]
    params = {}
    for node in nodes:
        params.update(pipeline[node].get("attach", {}))
    for extra in overrides:
        params.update(extra)
    return params


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.interface = run_tasks.load_interface()
        cls.training = load_jsonc(PIPELINE / "training.json")
        cls.grade = load_jsonc(PIPELINE / "grade.json")
        cls.match = load_jsonc(PIPELINE / "match.json")

    def test_shop_has_no_budget(self):
        self.assertNotIn("商店预算", self.interface.options)
        self.assertNotIn("商店预算", self.interface.tasks["Shop"]["option"])
        shop = load_jsonc(PIPELINE / "shop.json")
        self.assertNotIn("coin_budget", shop["SC.Shop.Config.Budget"]["attach"])      # 移除所有预算
        self.assertEqual(shop["SC.Shop.Config.Budget"]["attach"]["coin_reserve"], 100000)
        self.assertIn("商店余额保留", self.interface.tasks["Shop"]["option"])
        option = self.interface.options["商店余额保留"]
        self.assertEqual(option["default_case"], "10万")
        self.assertEqual([c["name"] for c in option["cases"]], ["不保留", "1万", "10万", "100万"])

    def test_training_settings_are_card_ticket_quantity_only(self):
        self.assertEqual(self.interface.tasks["Training"]["option"], ["训练卡", "训练券", "训练数量"])
        for gone in ("训练每日一次", "训练列表滚动"):
            self.assertNotIn(gone, self.interface.options)
        self.assertEqual([c["name"] for c in self.interface.options["训练券"]["cases"]], ["ブロンズトレーニングチケット"])
        self.assertEqual([c["name"] for c in self.interface.options["训练数量"]["cases"]], ["1"])
        params = entry_params(self.training, "SC.Training.Entry", [
            {"card_id": "2050170010"},   # input option "训练卡": run_tasks substitutes {card}
            attach_of(self.interface, "训练券", "ブロンズトレーニングチケット"), attach_of(self.interface, "训练数量", "1")])
        config = tt.TrainingConfig.from_params(params)
        # The bounded list scroll is fixed behaviour; a re-run trains again (the UI warns first).
        self.assertFalse(config.skip_if_done_today)
        self.assertEqual(config.max_scroll_steps, 10)  # 2026-10-11: whole-list search budget
        self.assertEqual((config.ticket, config.quantity), ("basic_bronze", 1))
        with self.assertRaises(Exception):
            tt.TrainingConfig.from_params({**params, "quantity": 2})

    def test_match_stage_defaults_to_the_latest_stage_of_the_progress(self):
        # 2026-10-11: default = the latest stage of the observed progress; 最新 Vo/Da/Vi stop with
        # stage_reward_unverified until the reward table is verified, so they are developer cases now.
        option = self.interface.options["Match关卡"]
        names = [c["name"] for c in option["cases"]]
        self.assertEqual(names[:3], ["最新Vo道具关卡", "最新Da道具关卡", "最新Vi道具关卡"])
        self.assertEqual(len(names), 3 + 25 + 1)
        self.assertEqual(option["default_case"], "最新（全部关卡）")
        self.assertNotIn("最新", names)
        for name in names:
            mt.MatchConfig.from_params(attach_of(self.interface, "Match关卡", name))
        self.assertEqual(attach_of(self.interface, "Match关卡", "エリア2 ステージ6")["stage"], "A2-S6")
        # PI default = Pipeline default = Python default
        self.assertEqual(self.match["SC.Match.Config.Stage"]["attach"]["stage"], "latest")
        self.assertEqual(mt.MatchConfig().stage, "latest")
        self.assertEqual(attach_of(self.interface, "Match关卡", option["default_case"])["stage"], "latest")
        notes = json.loads((ROOT / "ui" / "annotations.json").read_text(encoding="utf-8"))["options"]["Match关卡"]
        self.assertEqual(notes["quick_cases"], ["最新（全部关卡）"])
        self.assertEqual(set(notes["dev_cases"]), {"最新Vo道具关卡", "最新Da道具关卡", "最新Vi道具关卡"})
        # 只采样 defaults to No (user decision: Match uses the remaining rights); the spend still needs its flag
        self.assertEqual(self.interface.options["Match只采样"]["default_case"], "No")
        self.assertEqual(attach_of(self.interface, "Match只采样", "No"), {"sample_only": False})
        reserve = [c["name"] for c in self.interface.options["Match参加权保留"]["cases"]]
        self.assertEqual(reserve, [c["name"] for c in self.interface.options["Grade参加权保留"]["cases"]])
        self.assertEqual(reserve, ["0", "1", "5", "10"])
        for name in reserve:
            mt.MatchConfig.from_params(attach_of(self.interface, "Match参加权保留", name))
            gt.GradeConfig.from_params(attach_of(self.interface, "Grade参加权保留", name))
        task_options = self.interface.tasks["MatchSkip"]["option"]
        self.assertEqual(task_options.index("Match次数"), task_options.index("Match关卡") + 1)  # count right under the stage

    def test_grade_mode_count_to_twenty_and_bulk(self):
        options = self.interface.tasks["GradeFes"]["option"]
        self.assertNotIn("Grade彩排次数", options)
        self.assertEqual(options[0], "Grade模式")
        self.assertEqual(options.index("Grade一次消耗"), options.index("Grade每日次数") + 1)
        runs = [c["name"] for c in self.interface.options["Grade每日次数"]["cases"]]
        self.assertEqual(runs, [str(n) for n in range(1, 21)])
        self.assertEqual([c["name"] for c in self.interface.options["Grade一次消耗"]["cases"]], ["1", "2", "3", "4", "5"])
        self.assertNotIn("SC.Grade.Config.Rehearsal", self.grade)
        base = entry_params(self.grade, "SC.Grade.Entry")
        self.assertEqual(gt.GradeConfig.from_params(base), gt.GradeConfig())
        # 2026-10-11: 自动演出 on by default (Maa plays the live) so a spent right does not end in an uncertain observe-only run
        self.assertEqual(self.interface.options["Grade自动演出"]["default_case"], "开启")
        self.assertTrue(gt.GradeConfig.from_params({**base, **attach_of(self.interface, "Grade自动演出", "开启")}).live_play)
        twenty = gt.GradeConfig.from_params({**base, **attach_of(self.interface, "Grade每日次数", "20")})
        self.assertEqual((twenty.runs_per_day, twenty.grade_runs, twenty.rehearsal_runs), (20, 20, 0))
        rehearsal = gt.GradeConfig.from_params({**base, **attach_of(self.interface, "Grade模式", "フェスリハーサル"),
                                                **attach_of(self.interface, "Grade每日次数", "2")})
        self.assertEqual((rehearsal.grade_runs, rehearsal.rehearsal_runs), (0, 2))
        self.assertEqual(gt.GradeConfig.from_params({**base, "unit_score": "21801"}).unit_score, "21801")
        self.assertEqual(gt.GradeConfig.from_params({**base, "mode": "rehearsal", "unit_score": "21801"}).unit_score, "")
        notes = json.loads((ROOT / "ui" / "annotations.json").read_text(encoding="utf-8"))["options"]
        self.assertEqual(notes["Grade参加ユニット"]["show_when"], {"Grade模式": "グレードフェス"})
        bulk = gt.GradeConfig.from_params({**base, **attach_of(self.interface, "Grade一次消耗", "5")})
        self.assertEqual(bulk.bulk_count, 5)
        with self.assertRaises(Exception):
            gt.GradeConfig.from_params({**base, "bulk_count": 6})
        with self.assertRaises(Exception):
            gt.GradeConfig.from_params({**base, "runs_per_day": 21})


    def test_business_after_collect_reaches_the_runtime(self):
        """2026-10-11 user: collect, then 再営業へ and 決定 with the game's prefilled 12 h team, every day. 「收取后」
        maps to redispatch=decide (default) / back through SC.Business.Config.Redispatch; no team name is needed."""
        from agent.core import business_task as bt
        business = load_jsonc(PIPELINE / "business.json")
        option = self.interface.options["营业收取后"]
        self.assertEqual(option["default_case"], "再营业（默认）")
        self.assertEqual(self.interface.tasks["Business"]["option"][0], "营业收取后")
        self.assertIn("SC.Business.Config.Redispatch", business["SC.Business.Entry"]["custom_action_param"]["config_nodes"])
        base = entry_params(business, "SC.Business.Entry")
        self.assertEqual(bt.BusinessConfig.from_params(base).redispatch, bt.DEFAULT_REDISPATCH)
        decide = bt.BusinessConfig.from_params({**base, **attach_of(self.interface, "营业收取后", "再营业（默认）")})
        back = bt.BusinessConfig.from_params({**base, **attach_of(self.interface, "营业收取后", "只收取")})
        self.assertEqual((decide.redispatch, back.redispatch), ("decide", "back"))
        self.assertEqual((decide.team, decide.bulk_dispatch, decide.mode), ("", "off", "bulk_default"))
        plans = run_tasks.plan_tasks(self.interface, {"tasks": [{"name": "Business", "option": {"营业收取后": "只收取"}}]})
        self.assertEqual(run_tasks.task_config(plans[0], "business.json", bt.BusinessConfig).redispatch, "back")
        plans = run_tasks.plan_tasks(self.interface, {"tasks": [{"name": "Business"}]})
        self.assertEqual(run_tasks.task_config(plans[0], "business.json", bt.BusinessConfig).redispatch, "decide")


class FesAllocationTests(unittest.TestCase):
    POLICY = fa.TicketPolicy()

    def test_bulk_multiplies_demand_and_is_refused_until_sampled(self):
        grade = gt.GradeConfig(runs_per_day=3, bulk_count=2)
        allocation = fa.Allocation.from_configs(None, grade, order=("GradeFes",))
        self.assertEqual(allocation.grade_demand(), 6)
        plan = fa.plan(allocation, observed=(20, 20), policy=self.POLICY)
        self.assertIn("bulk_not_sampled", plan.errors)
        ok = fa.plan(fa.Allocation.from_configs(None, gt.GradeConfig(runs_per_day=3), order=("GradeFes",)),
                     observed=(20, 20), policy=self.POLICY)
        self.assertFalse(ok.refused)
        self.assertEqual(ok.grade, 3)

    def test_rehearsal_mode_plans_rehearsal_only(self):
        grade = gt.GradeConfig.from_params({"mode": "rehearsal", "runs_per_day": 2})
        allocation = fa.Allocation.from_configs(None, grade, order=("GradeFes",))
        self.assertEqual((allocation.grade_runs, allocation.rehearsal_runs), (0, 2))
        # Rehearsal was sampled 2026-10-11, so the plan no longer refuses it as unsampled.
        self.assertNotIn("rehearsal_not_sampled", fa.plan(allocation, policy=self.POLICY).errors)


def progress(*rows):
    return MatchProgress.from_dict({"cycle_id": "c1", "state": "ready", "stage_list_complete": True, "stages": [
        {"stage_id": f"A{a}-S{s}", "stage_order": order, "skip_available": skip} for a, s, order, skip in rows]})


class StageTargetTests(unittest.TestCase):
    def test_reward_table(self):
        self.assertEqual([n for n, a in mst.STAGE_REWARD.items() if a == "vo"], [1, 4, 13, 16, 19])
        self.assertEqual([n for n, a in mst.STAGE_REWARD.items() if a == "da"], [2, 5, 14, 17, 20])
        self.assertEqual([n for n, a in mst.STAGE_REWARD.items() if a == "vi"], [3, 6, 15, 18, 21])
        self.assertEqual(mst.attribute((2, 6)), "vi")
        self.assertIsNone(mst.attribute((1, 6)))       # stage 6 is in area 2: an inconsistent pair is unknown
        self.assertFalse(mst.REWARD_TABLE_VERIFIED)     # research table until native reward icons are sampled

    def test_latest_of_an_attribute_never_falls_back(self):
        p = progress((1, 1, 1, True), (2, 4, 4, True), (5, 13, 13, True), (5, 14, 14, True), (6, 16, 16, False))
        seen = {r.stage: r.skip_available for r in p.stages}
        self.assertEqual(mst.resolve("latest_vo", p.stages, seen), (None, "stage_reward_unverified"))
        self.assertEqual(mst.resolve("latest_vo", p.stages, seen, verified=True), (None, "latest_attribute_not_skippable"))
        self.assertEqual(mst.resolve("latest_da", p.stages, seen, verified=True), ((5, 14), None))
        self.assertEqual(mst.resolve("latest_vi", p.stages, seen, verified=True), (None, "attribute_stage_unavailable"))
        unseen = {**seen, (5, 14): False}
        self.assertEqual(mst.resolve("latest_da", p.stages, unseen, verified=True), (None, "latest_attribute_not_skippable"))
        with self.assertRaises(ValueError):
            mst.resolve("latest", p.stages, seen, verified=True)

    def test_planner_stops_on_the_unverified_table(self):
        p = progress((1, 1, 1, True), (1, 2, 2, True))
        planner = mt.SkipPlanner(mt.MatchConfig(stage="latest_vo", count=1, count_mode="fixed", sample_only=False), p)
        planner.seen = {(1, 1): True, (1, 2): True}
        decision = planner._choose(20)
        self.assertEqual((decision.kind, decision.outcome), ("abort", "stage_reward_unverified"))


class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = ui_schema.export_schema()

    LEGACY = {"schema_version": 1, "preset": "daily", "tasks": [
        {"name": "Shop", "enabled": True, "option": {"商店预算": "预算1000/保留100000", "商店跳过券": "Yes"}},
        {"name": "Training", "enabled": True, "option": {"训练每日一次": "No", "训练列表滚动": "4"}},
        {"name": "MatchSkip", "enabled": True, "option": {"Match关卡": "最新", "Match次数": "5"}},
        {"name": "GradeFes", "enabled": False, "option": {"Grade彩排次数": "2", "Grade每日次数": "1"}},
    ]}

    def test_python_migration(self):
        out = ui_schema.migrate_legacy_profile(self.schema, self.LEGACY)
        options = {t["name"]: t["option"] for t in out["tasks"]}
        self.assertEqual(options["Shop"], {"商店跳过券": "Yes"})
        self.assertEqual(options["Training"], {})
        self.assertEqual(options["MatchSkip"], {"Match关卡": "最新（全部关卡）", "Match次数": "5"})
        self.assertEqual(options["GradeFes"], {"Grade模式": "フェスリハーサル", "Grade每日次数": "2"})
        config = ui_schema.profile_to_run_config(self.schema, self.LEGACY)  # removed keys no longer fail the config
        shop = next(t for t in config["tasks"] if t["name"] == "Shop")
        self.assertNotIn("商店预算", shop.get("option", {}))
        zero = {**self.LEGACY, "tasks": [{"name": "GradeFes", "enabled": False, "option": {"Grade彩排次数": "0"}}]}
        self.assertEqual(ui_schema.migrate_legacy_profile(self.schema, zero)["tasks"][0]["option"], {})

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_migration_mirrors_python(self):
        script = (
            "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
            "const { migrateLegacyProfile } = await import('./ui/components/store.js');"
            "const { readFileSync } = await import('node:fs');"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            f"const legacy = {json.dumps(self.LEGACY, ensure_ascii=False)};"
            "console.log(JSON.stringify(migrateLegacyProfile(schema, legacy)));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ui_schema.migrate_legacy_profile(self.schema, self.LEGACY))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class UiControlTests(unittest.TestCase):
    PRELUDE = (
        "import './tests/fixtures/ui/fake_dom.mjs';"
        "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
        "const { readFileSync } = await import('node:fs');"
        "const of = await import('./ui/components/option-field.js');"
        "const d = await import('./ui/components/display.js');"
        "const tp = await import('./ui/components/task-panel.js');"
        "const { fesPlan } = await import('./ui/components/fes-plan.js');"
        "const { profileFromPreset } = await import('./ui/components/store.js');"
        "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
        "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
        "const state = { schema, annotations, prefs: {}, run: { active: false }, taskStatus: {}, expanded: [] };"
        "const task = (n) => schema.tasks.find((t) => t.name === n);"
        "const find = (el, pred) => { if (pred(el)) return el; for (const c of el.childNodes || []) { const f = find(c, pred); if (f) return f; } return null; };"
        "const all = (el, pred, out = []) => { if (pred(el)) out.push(el); for (const c of el.childNodes || []) all(c, pred, out); return out; };"
        "const cls = (el, name) => (el.attributes?.class || '').split(' ').includes(name);")

    def run_js(self, body):
        result = subprocess.run(["node", "--input-type=module", "-e", self.PRELUDE + body], cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_stepper_sequence_and_keyboard(self):
        out = self.run_js(
            "const seq = of.stepSequence(schema.options['Match次数'].cases);"
            "const changes = [];"
            "const field = of.optionField({ state, key: 'Match次数', value: '20', disabled: false, onChange: (v) => changes.push(v), focusKey: 'k' });"
            "const spin = find(field, (n) => n.attributes?.role === 'spinbutton');"
            "for (const key of ['ArrowUp', 'ArrowDown', 'PageDown', 'Home', 'End']) spin.listeners.keydown[0]({ key, preventDefault() {} });"
            "const chips = all(field, (n) => cls(n, 'chip')).map((c) => [c.textContent, c.attributes['aria-pressed']]);"
            "const g1 = of.optionField({ state, key: 'Grade每日次数', value: '1', disabled: false, onChange() {}, focusKey: 'g' });"
            "const minus = find(g1, (n) => n.attributes?.['aria-label'] === '减少');"
            "console.log(JSON.stringify({ seq, changes, chips, kind: field.attributes.class, valuenow: spin.attributes['aria-valuenow'],"
            " minusDisabled: 'disabled' in minus.attributes, tab: minus.attributes.tabindex,"
            " step: [of.stepValue(seq, '1', -1), of.stepValue(seq, '20', 1), of.stepValue(seq, 'x', 1)] }));")
        self.assertEqual(out["seq"], [str(n) for n in range(1, 21)] + ["全部用完"])
        self.assertEqual(out["changes"], ["全部用完", "19", "15", "1", "全部用完"])
        self.assertEqual(out["chips"], [["1", "false"], ["5", "false"], ["10", "false"], ["20", "true"], ["全部", "false"]])
        self.assertIn("kind-stepper", out["kind"])
        self.assertEqual(out["valuenow"], "20")
        self.assertTrue(out["minusDisabled"])
        self.assertEqual(out["tab"], "-1")          # −/+ are pointer helpers; the spinbutton is the keyboard control
        self.assertEqual(out["step"], ["1", "全部用完", "2"])

    def test_stage_target_quick_and_custom(self):
        out = self.run_js(
            "const changes = [];"
            "const quick = of.optionField({ state, key: 'Match关卡', value: '最新（全部关卡）', disabled: false, onChange: (v) => changes.push(v), focusKey: 's' });"
            "const radios = all(quick, (n) => n.attributes?.role === 'radio');"
            "radios[1].listeners.click[0]();"
            "const custom = of.optionField({ state, key: 'Match关卡', value: 'エリア2 ステージ6', disabled: false, onChange() {}, focusKey: 's' });"
            "const grid = all(custom, (n) => cls(n, 'stage-grid') ).flatMap((g) => all(g, (n) => n.attributes?.role === 'radio'));"
            "console.log(JSON.stringify({ labels: radios.map((r) => [r.textContent, r.attributes['aria-checked']]), changes,"
            " quickSelect: !!find(quick, (n) => n.tagName === 'SELECT') || !!find(quick, (n) => cls(n, 'stage-grid')), options: grid.length,"
            " anySelect: !!find(custom, (n) => n.tagName === 'SELECT'),"
            " chosen: grid.filter((o) => o.attributes['aria-checked'] === 'true').map((o) => [o.textContent, o.attributes.title]),"
            " customChecked: all(custom, (n) => n.attributes?.role === 'radio' && cls(n, 'segment')).map((r) => r.attributes['aria-checked']),"
            " visibleHint: !!find(quick, (n) => cls(n, 'field-hint')),"
            " hint: all(quick, (n) => n.attributes?.title).map((n) => n.attributes.title).join('|') }));")
        self.assertEqual(out["labels"], [["最新", "true"], ["自定义", "false"]])
        self.assertEqual(out["changes"], ["エリア1 ステージ1"])
        self.assertFalse(out["quickSelect"])
        self.assertEqual(out["options"], 25)                          # 最新 Vo/Da/Vi are developer cases
        self.assertFalse(out["anySelect"])                            # 2026-10-24: one pill per stage, no dropdown
        self.assertEqual(out["chosen"], [["2-6", "2-6 · Vi 道具"]])    # stage on the pill (reward dot), full label as tooltip
        self.assertEqual(out["customChecked"], ["false", "true"])
        self.assertIn("能跳过的最新一关", out["hint"])     # 2026-10-24: the explanation is a tooltip, not a line
        self.assertFalse(out["visibleHint"])

    def test_auto_unit_grid_and_book_visibility(self):
        # 2026-10-30 (user): Auto ユニット like the W.I.N.G. team bar (20 numbered pills, 「将覆盖ユニットN」 under 自动编队);
        # 带书 only for S.T.E.P.; no 核对ユニット名 / 育成方針「不改」
        out = self.run_js(
            "const field = (siblings) => of.optionField({ state, key: 'Auto编队', value: 'ユニット20', disabled: false, onChange() {},"
            " focusKey: 'u', siblings });"
            "const auto = field({ 'Auto编队来源': '自动编队' }), unit = field({ 'Auto编队来源': '指定ユニット' });"
            "const pills = all(auto, (n) => n.attributes?.role === 'radio');"
            "const vis = (option) => d.visibleOptions(state, task('WeeklyProduce'), { name: 'WeeklyProduce', option });"
            "console.log(JSON.stringify({ kind: auto.attributes.class, n: pills.length, on: pills.filter((p) => p.attributes['aria-checked'] === 'true')"
            ".map((p) => [p.textContent, p.attributes.title]), badge: find(auto, (n) => cls(n, 'team-overwrite'))?.textContent ?? null,"
            " noBadge: !find(unit, (n) => cls(n, 'team-overwrite')), select: !!find(auto, (n) => n.tagName === 'SELECT'),"
            " wing: vis({}), step: vis({ 'Auto核对剧本': 'S.T.E.P.' }) }));")
        self.assertIn("kind-units", out["kind"])
        self.assertEqual(out["n"], 20)
        self.assertEqual(out["on"], [["20", "ユニット20"]])
        self.assertEqual(out["badge"], "将覆盖ユニット20")
        self.assertTrue(out["noBadge"])
        self.assertFalse(out["select"])
        self.assertNotIn("Auto带书", out["wing"])
        self.assertIn("Auto带书", out["step"])
        self.assertNotIn("Auto核对ユニット名", out["step"])
        weekly = json.loads((ROOT / "assets" / "tasks" / "weekly_produce.json").read_text(encoding="utf-8"))["option"]
        cases = [c["name"] for c in weekly["Auto育成方針"]["cases"]]
        self.assertEqual(cases, ["自动", "Vocal", "Dance", "Visual"])

    def test_task_layouts_and_visible_settings(self):
        out = self.run_js(
            "const profile = profileFromPreset(schema, 'daily');"
            "const s = { ...state, profile };"
            "const entry = (n) => profile.tasks.find((t) => t.name === n);"
            "const vis = (n) => d.visibleOptions(s, task(n), entry(n));"
            "console.log(JSON.stringify({ training: vis('Training'), match: vis('MatchSkip'), grade: vis('GradeFes'), shop: vis('Shop').includes('商店预算'),"
            " matchGroups: tp.optionGroups(s, task('MatchSkip'), entry('MatchSkip')).length,"
            " gradeGroups: tp.optionGroups(s, task('GradeFes'), entry('GradeFes')).length,"
            " ticket: of.optionField({ state: s, key: '训练券', value: 'ブロンズトレーニングチケット', disabled: false, onChange() {}, focusKey: 't' }).textContent,"
            " qty: (() => { const f = of.optionField({ state: s, key: '训练数量', value: '1', disabled: false, onChange() {}, focusKey: 'q' });"
            " return [f.textContent, ...all(f, (n) => n.attributes?.title).map((n) => n.attributes.title)].join('|'); })() }));")
        self.assertEqual(out["training"], ["训练卡", "训练券", "训练数量"])
        self.assertEqual(out["match"][:5], ["Match推进", "Match编队", "Match失败重试", "Match关卡", "Match次数"])
        # Both Grade and rehearsal are supported; only unimplemented bulk/recovery controls stay hidden.
        # 2026-10-26 (user: 勾选即同意): the Grade tick is the 参加权 consent, so its switch is hidden too.
        self.assertEqual(out["grade"][:4], ["Grade模式", "Grade难度", "Grade每日次数", "Grade参加ユニット"])
        for hidden in ("Grade使用参加权", "Grade一次消耗", "Grade回复券", "Grade回复券上限", "Grade采样每日上限"):
            self.assertNotIn(hidden, out["grade"])
        for hidden in ("Match回复券", "Match回复券上限", "Match只采样"):
            self.assertNotIn(hidden, out["match"])
        self.assertFalse(out["shop"])
        self.assertEqual((out["matchGroups"], out["gradeGroups"]), (0, 0))
        self.assertIn("铜训练券", out["ticket"])
        self.assertIn("每天 1 张", out["qty"])

    def test_fes_plan_mirrors_bulk_and_mode(self):
        out = self.run_js(
            "const profile = profileFromPreset(schema, 'daily');"
            "const grade = profile.tasks.find((t) => t.name === 'GradeFes');"
            "grade.enabled = true; grade.option = { ...grade.option, 'Grade使用参加权': 'Yes', 'Grade每日次数': '3', 'Grade一次消耗': '2' };"
            "const bulk = fesPlan({ ...state, profile });"
            "grade.option = { ...grade.option, 'Grade一次消耗': '1', 'Grade模式': 'フェスリハーサル' };"
            "const rehearsal = fesPlan({ ...state, profile });"
            "console.log(JSON.stringify({ bulk: [bulk.errors, bulk.summary], rehearsal: [rehearsal.errors, rehearsal.grade, rehearsal.rehearsal] }));")
        self.assertEqual(out["bulk"][0], ["bulk_not_sampled"])
        self.assertIn("Grade 3×2", out["bulk"][1])
        self.assertEqual(out["rehearsal"], [[], 0, 3])

    def test_grade_runs_toggles_and_business_rows(self):
        """Grade daily targets match the runtime range (1–20); 自动演出/倍速 are toggles
        that keep their case names; 营业 shows one 「收取后」 choice and no team field; 任务奖励/礼物箱 hide their 关闭 case."""
        out = self.run_js(
            "const changes = [];"
            "const runs = of.optionField({ state, key: 'Grade每日次数', value: '1', disabled: false, onChange() {}, focusKey: 'r' });"
            "const spin = find(runs, (n) => n.attributes?.role === 'spinbutton');"
            "const live = of.optionField({ state, key: 'Grade自动演出', value: '开启', disabled: false, onChange: (v) => changes.push(v), focusKey: 'l' });"
            "const sw = find(live, (n) => n.attributes?.role === 'switch');"
            "sw.listeners.click[0]();"
            "const off = of.optionField({ state, key: 'Grade自动演出', value: '关闭', disabled: false, onChange: (v) => changes.push(v), focusKey: 'l' });"
            "find(off, (n) => n.attributes?.role === 'switch').listeners.click[0]();"
            "const speed = of.optionField({ state, key: 'Grade倍速', value: '保持', disabled: false, onChange: (v) => changes.push(v), focusKey: 'v' });"
            "find(speed, (n) => n.attributes?.role === 'switch').listeners.click[0]();"
            "const e = { enabled: true, option: {} };"
            "console.log(JSON.stringify({ max: spin.attributes['aria-valuemax'], checked: sw.attributes['aria-checked'], changes,"
            " business: d.visibleOptions(state, task('Business'), e), missions: d.visibleOptions(state, task('MissionsClaim'), e),"
            " present: d.visibleCases(state, 'プレゼント领取', undefined).map((c) => c.name),"
            " devBusiness: d.visibleOptions({ ...state, prefs: { developer: true } }, task('Business'), e).length }));")
        self.assertEqual(out["max"], "20")
        self.assertEqual(out["checked"], "true")
        self.assertEqual(out["changes"], ["关闭", "开启", "开启"])
        self.assertEqual(out["business"], ["营业收取后"])
        self.assertEqual(out["missions"], ["任务奖励领取"])         # 2026-10-15: デイリー+ウィークリー / 仅デイリー choice
        self.assertNotIn("关闭", out["present"])
        self.assertEqual(out["devBusiness"], 6)

    def test_rows_have_no_right_side_item_icon(self):
        panel = (ROOT / "ui" / "components" / "task-panel.js").read_text(encoding="utf-8")
        self.assertNotIn("itemIcon(", panel)
        self.assertIn("const tail = status?.status ? statusPill(status.status) : null;", panel)


class ShopIconTests(unittest.TestCase):
    def test_every_other_goods_entry_names_its_own_item(self):
        annotations = json.loads((ROOT / "ui" / "annotations.json").read_text(encoding="utf-8"))
        from tools import ui_game_assets as build
        items = {k: v["item"] for k, v in annotations["options"].items() if k.startswith("商店其他·")}
        self.assertEqual(len(items), 25)
        self.assertEqual(len(set(items.values())), 25)         # one icon per goods, no shared placeholder
        game_assets = (ROOT / "ui" / "components" / "game-assets.js").read_text(encoding="utf-8")
        pinned = set(build.TILE_ITEMS) | set(build.ATLAS_ITEMS)
        for key, item in items.items():
            self.assertTrue(item in pinned or f"  {item}: {{" in game_assets, (key, item))
        self.assertGreaterEqual(sum(item in pinned for item in items.values()), 15)


if __name__ == "__main__":
    unittest.main()
