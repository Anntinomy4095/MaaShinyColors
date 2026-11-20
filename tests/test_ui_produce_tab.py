"""2026-10-13: 「Produce」 tab (W.I.N.G. apart from the daily queue) and the hidden automatic 每日任务进度 read."""
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"

SETUP = """
import { readFileSync } from 'node:fs';
const D = await import('./ui/components/display.js');
const S = await import('./ui/components/store.js');
const schema = JSON.parse(readFileSync('ui/fixtures/schema.json','utf8'));
const annotations = JSON.parse(readFileSync('ui/annotations.json','utf8'));
const placement = D.taskPlacement(annotations, schema);
const state = (profile, extra = {}) => ({ schema, annotations, profile, prefs: {}, run: { active: false }, taskStatus: {}, ...extra });
const enabled = (p) => S.profileToRunConfig(schema, p).tasks.filter((t) => t.enabled).map((t) => t.name);
"""


def run_js(body, dom=False):
    prelude = "import './tests/fixtures/ui/fake_dom.mjs';\n" if dom else ""
    result = subprocess.run(["node", "--input-type=module", "-e", prelude + SETUP + body], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class PlacementTests(unittest.TestCase):
    def test_saved_queue_with_wing_and_mission_reader_migrates(self):
        out = run_js("""
const stored = { schema_version: 1, preset: 'daily', removed_tasks: ['MissionStatus'], tasks: [
  { name: 'Shop', enabled: true, option: {} },
  { name: 'AutoProduce', enabled: true, option: { 'Produce主属性': 'Vo' } },
  { name: 'MissionStatus', enabled: false, option: {} }] };
const profile = S.applyTaskPlacement(S.sanitizeProfile(schema, stored, 'daily'), placement);
const auto = profile.tasks.find((t) => t.name === 'AutoProduce');
console.log(JSON.stringify({ placement, auto, mission: profile.tasks.find((t) => t.name === 'MissionStatus').enabled,
  removed: profile.removed_tasks, shown: profile.tasks.filter((t) => D.taskShown(state(profile), t.name)).map((t) => t.name) }));
""")
        self.assertEqual(out["placement"], {"auto": ["MissionStatus"], "own": ["ResourceRead", "AutoProduce"]})
        self.assertFalse(out["auto"]["enabled"])
        self.assertEqual(out["auto"]["option"]["Produce主属性"], "Vo")  # options kept for the 「Produce」 tab
        self.assertTrue(out["mission"])
        self.assertNotIn("MissionStatus", out["removed"])
        self.assertNotIn("AutoProduce", out["shown"])
        self.assertNotIn("MissionStatus", out["shown"])
        self.assertIn("Shop", out["shown"])

    def test_daily_run_adds_the_reader_and_never_runs_wing(self):
        out = run_js("""
const profile = S.profileFromPreset(schema, 'daily');
profile.tasks.find((t) => t.name === 'AutoProduce').enabled = true;  // e.g. an old 我的方案
const daily = D.dailyRunState(state(profile)).profile;
const none = { ...profile, tasks: profile.tasks.map((t) => ({ ...t, enabled: false })) };
const single = D.singleTaskState(state(profile), 'Shop').profile;
console.log(JSON.stringify({ daily: enabled(daily), none: enabled(D.dailyRunState(state(none)).profile), single: enabled(single),
  blockers: D.startBlockers(state(profile)).map((b) => b.name) }));
""")
        self.assertNotIn("AutoProduce", out["daily"])
        # 2026-10-14 user: 任务奖励 (in the daily preset) reads the page itself; the separate MissionStatus read is skipped
        self.assertNotIn("MissionStatus", out["daily"])
        self.assertIn("MissionsClaim", out["daily"])
        self.assertEqual(out["none"], [])  # nothing ticked: no lone automatic read
        self.assertEqual(out["single"], ["Shop"])  # 仅运行此任务 stays a single task
        self.assertNotIn("AutoProduce", out["blockers"])

    def test_wing_run_state_and_run_bar(self):
        out = run_js("""
const profile = S.profileFromPreset(schema, 'daily');  // AutoProduce is outside the preset (removed_tasks)
const wing = D.wingRunState(state(profile));
const bar = (extra) => D.wingRunBar(state(profile, extra));
console.log(JSON.stringify({ run: enabled(wing.profile), only: D.wingOnly(wing), spend: S.profileSpendFlags(schema, wing.profile),
  start: bar({}), daily: bar({ run: { active: true, mode: 'supervised_daily', tasks: ['Shop'] } }),
  wingRun: bar({ run: { active: true, mode: 'supervised_wing', tasks: ['AutoProduce'] } }),
  dev: bar({ prefs: { developer: true }, runMode: 'offline_replay' }) }));
""")
        self.assertEqual(out["run"], ["AutoProduce"])
        self.assertTrue(out["only"])
        self.assertEqual(out["spend"], ["allow_produce_spend"])  # 可开新局 fixed to Yes: 开始 on 「Produce」 is the consent
        self.assertEqual(out["start"]["kind"], "start")
        self.assertTrue(out["start"]["disabled"])  # no P card chosen yet
        self.assertIn("体力 30", out["start"]["sub"])
        self.assertIn("核对", out["start"]["tip"])   # 2026-10-24: the resume rule is the button tooltip
        self.assertEqual(out["start"]["main"], "W.I.N.G. · True End")
        self.assertEqual(out["daily"]["kind"], "busy")
        self.assertEqual(out["wingRun"]["kind"], "stop")
        self.assertEqual(out["dev"]["devRun"], "offline_replay")


class StaticWiringTests(unittest.TestCase):
    def test_tab_uses_the_existing_wing_run_path(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        panel = (UI / "components" / "wing-panel.js").read_text(encoding="utf-8")
        self.assertIn("startWing: async () => {", app)
        self.assertIn("return actions.startRun(WING_TASK);", app)  # ready card details → straight to the run
        self.assertIn("if (cardDetailsStatus(host).ready) actions.startRun(WING_TASK);", app)
        self.assertIn('const mode = wing ? "supervised_wing" : devRun || "supervised_daily";', app)
        self.assertNotIn("openWingRunConfirm(", app)  # 2026-10-13: the saved 开新局 switch is the consent, no dialog
        self.assertIn("actions.startWing()", panel)
        for word in ("bridge", "request(", "postMessage"):
            self.assertNotIn(word, panel)
        self.assertIn('customElements.define("sc-wing-panel", WingPanel);', panel)

    def test_no_start_section_and_difficulty_is_segmented(self):
        # 2026-10-13 (user): the 「开始」 section (开局方式, incl. the disabled 放弃并重开) is gone; 难度 uses segmented buttons
        panel = (UI / "components" / "wing-panel.js").read_text(encoding="utf-8")
        for word in ("startModeField", "放弃并重开", "开局方式"):
            self.assertNotIn(word, panel)
        notes = json.loads((UI / "annotations.json").read_text(encoding="utf-8"))["options"]
        self.assertTrue(notes["Produce可开新局"].get("hidden"))
        self.assertNotIn("heading", notes["Produce可开新局"])
        self.assertEqual(notes["Produce难度"].get("control"), "segmented")


class ScenarioRailTests(unittest.TestCase):
    def test_rail_lists_game_scenarios_with_placeholders(self):
        # 2026-10-25 (user): later scenarios appear as placeholders on the Produce tab, 育成 then 特化
        out = run_js("""
const W = await import('./ui/components/wing-run.js');
console.log(JSON.stringify({ one: W.scenarioItems(['wing']), two: W.scenarioItems(['wing', 'step'], 'step') }));
""")
        self.assertEqual([s["label"] for s in out["one"]], ["W.I.N.G.", "感謝祭", "G.R.A.D.", "Landing Point", "S.T.E.P.", 'say "Halo"'])
        self.assertEqual([s["group"] for s in out["one"]], ["growth"] * 4 + ["special"] * 2)
        self.assertEqual([s["key"] for s in out["one"] if s["ready"]], ["wing"])
        self.assertEqual([s["key"] for s in out["one"] if s["current"]], ["wing"])
        self.assertEqual(out["one"][1]["title"], "ファン感謝祭：未开放")
        self.assertEqual(out["one"][0]["title"], "W.I.N.G.")
        self.assertEqual([s["key"] for s in out["two"] if s["current"]], ["step"])
        panel = (UI / "components" / "wing-panel.js").read_text(encoding="utf-8")
        self.assertIn("scenarioRail(state, entry, actions, running), summaryCard(", panel)
        self.assertNotIn('"wing-hero-name"', panel)   # the lit rail item names the scenario


    def test_halo_scenario(self):
        # H2c batch 5: the rail switches 「Produce剧本」; say "Halo" shows 路线 / 带书, hides W.I.N.G.-only fields
        out = run_js("""
const W = await import('./ui/components/wing-run.js');
const entry = (option) => ({ name: 'AutoProduce', enabled: true, option });
const halo = entry({ 'Produce剧本': 'say "Halo"', 'Halo带书': '設定履歴' });
const s = state({ tasks: [halo] });
const t = schema.tasks.find((x) => x.name === 'AutoProduce');
const wing = entry({});
console.log(JSON.stringify({
  scenario: [D.wingScenario(s, halo), D.wingScenario(s, wing)],
  summary: W.wingSummary(s, halo).map((f) => [f.key, f.value]),
  fold: W.sectionSummary(s, halo, '培养目标'),
  shown: D.visibleOptions(s, t, halo),
  wingShown: D.visibleOptions(state({ tasks: [wing] }), t, wing),
  bar: D.wingRunBar(state({ tasks: [entry({ 'Produce剧本': 'say "Halo"', 'Produce次数': '3' })] })),
  blockers: D.wingBlockers(state({ tasks: [entry({ 'Produce剧本': 'say "Halo"', 'Produce次数': '3', 'Produce报酬后OK': 'No' })] })),
}));
""", dom=True)
        self.assertEqual(out["scenario"], ["halo", "wing"])
        self.assertEqual(out["summary"], [["book", "設定履歴"], ["p", "未选择"], ["team", "ユニット1"]])
        self.assertEqual(out["fold"], "設定履歴")         # 掘书线 only since 2026-11-07: no route row
        self.assertIn("Halo带书", out["shown"])
        self.assertNotIn("Halo带书", out["wingShown"])
        self.assertNotIn("Halo路线", out["shown"] + out["wingShown"])
        for key in ("Produce目标", "Produce难度", "Produce次数", "ProduceSP加点", "Produce试镜出牌"):
            self.assertNotIn(key, out["shown"])
            self.assertIn(key, out["wingShown"])
        self.assertNotIn("Produce剧本", out["shown"] + out["wingShown"])
        self.assertIn("Produce报酬读取", out["shown"])
        self.assertEqual((out["bar"]["main"], out["bar"]["sub"]), ('say "Halo"', "新局消耗：体力 30"))
        self.assertNotIn("连跑需开启「读取后自动确认」", [e for b in out["blockers"] for e in b["errors"]])
        panel = (UI / "components" / "wing-panel.js").read_text(encoding="utf-8")
        self.assertIn("actions.setOption(WING_TASK, SCENARIO_OPTION", panel)


    def test_halo_summary_card_and_goals(self):
        # 2026-11-15 (user 「UI上也没有任何选项」): choosing say "Halo" threw in summaryCard (no 目标 fact) and the page
        # showed 「此页出错了」 instead of the settings; the goals come from the ノウハウ wanted list.
        out = run_js("""
globalThis.HTMLElement ??= class {}; globalThis.customElements ??= { define() {}, get() {} };
const P = await import('./ui/components/wing-panel.js');
const W = await import('./ui/components/wing-run.js');
const entry = (option) => ({ name: 'AutoProduce', enabled: true, option });
const card = (option) => { const e = entry(option); return P.summaryCard(state({ tasks: [e] }), e, null).textContent; };
const item = (name, purpose = 'any') => ({ name, match: 'prefix', min_level: 1, purpose });
console.log(JSON.stringify({
  halo: card({ 'Produce剧本': 'say "Halo"' }), wing: card({}),
  none: W.haloGoalsLine([]), step: W.haloGoalsLine([item('施設効率相談', 'step')]), bad: W.haloGoalsLine([item('Vocal適正◎', 'halo')]),
  full: W.haloGoalsLine([item('施設効率相談+', 'halo'), item('昂揚')]),
  half: W.haloGoals([item('パッシブスキル延長'), item('ファンの声援')]),
}));
""", dom=True)
        self.assertIn("掘书线", out["halo"])
        self.assertIn("带书 おまかせ", out["halo"])
        self.assertIn("True End", out["wing"])
        # no Halo item = the default goals (halo_policy.Goals()); S.T.E.P.-only items do not count
        self.assertEqual(out["none"], {"text": "目标（默认）：施設効率相談 · 施設効率相談+ · チーム完成度 100%", "warn": False})
        self.assertEqual(out["step"], out["none"])
        self.assertTrue(out["bad"]["warn"])           # Halo items, none for the dig route: refused (tools/ui_wing)
        self.assertEqual(out["full"], {"text": "目标：施設効率相談 · 施設効率相談+ · チーム完成度 100% · 粉丝不超 10 万", "warn": False})
        self.assertEqual(out["half"], {"facility": False, "facilityPlus": False, "completion": 51, "fansCap": False})


class CopyAuditTests(unittest.TestCase):
    """2026-10-13 copy audit: summary facts, per-choice notes, inactive settings, recommendation prerequisite and reason."""

    def test_summary_notes_and_inactive_settings(self):
        out = run_js("""
const W = await import('./ui/components/wing-run.js');
const R = await import('./ui/components/wing-run.js');
const entry = (option) => ({ name: 'AutoProduce', enabled: true, option });
const s = state({ tasks: [entry({})] });
console.log(JSON.stringify({
  summary: W.wingSummary(s, entry({})).map((f) => [f.label, f.value]),
  recSummary: W.wingSummary(s, entry({ 'Produce编队方式': '自动编队', 'Produce编队槽位': 'unit3' })).map((f) => [f.label, f.value, Boolean(f.missing)]),
  searchSummary: W.wingSummary(s, entry({ 'Produce找编队': 'Yes' })).find((f) => f.key === 'team').value,
  keys: W.wingSummary(s, entry({ 'Produce可开新局': 'No' })).map((f) => f.key),
  folds: ['开始', '培养目标', '试镜'].map((h) => W.sectionSummary(s, entry({}), h)),
  exports: Object.keys(W),
  styleNote: W.caseNote(s, entry({ 'Produce出牌风格': '按属性' }), 'Produce出牌风格'),
  styleLabels: ['稳拿第一', '按属性'].map((c) => D.caseLabel(s, 'Produce出牌风格', c)),
  goal: W.caseNote(s, entry({ 'Produce目标': '只过季门槛' }), 'Produce目标'),
  live: W.caseNote(s, entry({ 'Produce试镜出牌': '游戏 Auto', 'Produce高级Auto门槛': '99%' }), 'Produce试镜出牌'),
  inactiveSkip: Object.keys(W.inactiveSettings(s, entry({ 'ProduceSP加点': '不加点' }))),
  inactiveAuto: Object.keys(W.inactiveSettings(s, entry({}))),
  primary: [R.primaryLine('Vi', ''), R.primaryLine('自动', '')],
}));
""", dom=True)
        # 2026-10-13 (user): no 开局 fact, no 「开始」 fold, no 开局方式 helpers (a run always resumes after the check or starts one)
        self.assertEqual(out["summary"], [["目标", "True End"], ["主属性", "跟随 P 偶像"], ["P 偶像", "未选择"], ["编队", "ユニット1"]])
        self.assertNotIn("start", out["keys"])
        # 2026-10-14 (user): 推荐编队 overwrites the chosen unit; Maa picks the P card (not a missing user choice)
        self.assertEqual(out["recSummary"][3], ["编队", "推荐编队 · 覆盖ユニット3", False])
        self.assertEqual(out["recSummary"][2], ["P 偶像", "推荐后显示", False])
        self.assertEqual(out["searchSummary"], "自动查找编队")
        self.assertEqual(out["folds"][0], "")
        self.assertIn("Middle", out["folds"][1])
        self.assertIn("主攻流行 1 位", out["folds"][2])
        self.assertFalse({"START_MODES", "startMode"} & set(out["exports"]))
        # B: 出牌目标 labels and the note matching the scorer (no focus bonus = balanced, by expected stars)
        self.assertEqual(out["styleLabels"], ["主攻流行 1 位", "平衡"])
        self.assertIn("期望星数", out["styleNote"])
        self.assertIn("不追 50 万", out["goal"])
        self.assertIn("当前 99%", out["live"])
        self.assertEqual(sorted(out["inactiveSkip"]), sorted(["Produce上限余量", "Produce终局清空SP", "Produce保护Lv5书"]))
        self.assertEqual(out["inactiveAuto"], ["Produce保护Lv5书"])     # the 保護 target is unverified: never clicked
        self.assertEqual(out["primary"], ["本次：Vi", "选择 P 偶像后，跟随其特长决定。"])

    def test_minigame_option_line(self):
        """「开局前大富豪」: 自动 reads the adopted 推荐编队's score; 指定ユニット has none (not played)."""
        out = run_js("""
const W = await import('./ui/components/wing-run.js');
const P = await import('./ui/components/team-proposal.js');
const s = ['2010010020', '2020020020', '2030030020', '2060060020', '2070070020'];
const cards = { 'ProduceP卡': '1010010010', 'ProduceS卡1': s[0], 'ProduceS卡2': s[1], 'ProduceS卡3': s[2], 'ProduceS卡4': s[3], 'Produce客串': s[4] };
const prop = (rating) => P.cleanProposal({ version: 1, status: 'needs_confirmation', requested_p_card: 'auto',
  proposal: { p_card: '1010010010', s_cards: s.slice(0, 4), guest: s[4] }, engine: 'true_end',
  recommender: { ProxyTERating: rating, growth: { route_feasible: true }, battle: { rows: [] } } });
const entry = (option) => ({ name: 'AutoProduce', enabled: true, option });
const st = state({ tasks: [entry({})] });
const rec = (game) => entry({ 'Produce编队方式': '自动编队', 'Produce大富豪': game, ...cards });
console.log(JSON.stringify({
  def: st.schema.options['Produce大富豪'].default_case, cases: st.schema.options['Produce大富豪'].cases.map((c) => c.name),
  low: W.minigameLine(st, rec('自动'), prop(60)), high: W.minigameLine(st, rec('自动'), prop(90)),
  none: W.minigameLine(st, rec('自动'), null), unit: W.minigameLine(st, entry({ ...cards }), prop(60)),
  off: W.minigameLine(st, rec('关'), prop(60)), always: W.minigameLine(st, rec('每次'), prop(60)),
  auto: P.minigameAuto(prop(60), { p_card: '1010010010', s_cards: s.slice(0, 4), guest: s[4] }, true),
  fold: W.sectionSummary(st, entry({}), '事件与道具'),
}));
""")
        self.assertEqual((out["def"], out["cases"]), ("自动", ["关", "自动", "每次"]))
        self.assertIn("推荐评分 60.0", out["low"])
        self.assertIn("本次开局前玩一局", out["low"])          # entry live-verified 2026-10-15
        self.assertEqual(out["high"], "推荐评分 90.0：本次不玩。")
        self.assertIn("无评分", out["none"])
        self.assertEqual(out["unit"], "指定ユニット没有推荐评分：本次不玩。")
        self.assertEqual((out["off"], out["always"]), ("", ""))
        self.assertEqual(out["auto"], {"play": True, "rating": 60})
        self.assertEqual(out["fold"], "事件选第 1 项 · 大富豪自动")

    def test_recommend_prerequisite_and_reason(self):
        out = run_js("""
const P = await import('./ui/components/team-proposal.js');
const s = ['2010010020', '2020020020', '2030030020', '2060060020', '2070070020'];
const owned = { p: ['1010010010'], s };
const rec = { version: 1, status: 'needs_confirmation', primary: 'Vo', engine: 'true_end', owned_counts: { p: 1, s: 5 },
  proposal: { p_card: '1010010010', s_cards: s.slice(0, 4), guest: s[4] },
  recommender: { ProxyTERating: 76.69, growth: { effective_stats: { Vo: 663.2 }, sp: 322.2, sp_required: 280, route_feasible: true },
    battle: { rows: [] } } };
const clean = P.cleanProposal(rec);
console.log(JSON.stringify({
  never: P.recommendReadiness({ p: [], s: [] }), onlyP: P.recommendReadiness({ p: ['1010010010'], s: [] }).kind,
  few: P.recommendReadiness({ p: ['1010010010'], s: s.slice(0, 4) }).kind, ready: P.recommendReadiness(owned, '1010010010').ok,
  notOwnedP: P.recommendReadiness(owned, '1020010010').kind,
  why: P.proposalWhy(clean), outdated: [P.proposalOutdated(clean, owned), P.proposalOutdated(clean, { p: owned.p, s: [...s, '2080080020'] })],
  scan: [P.noProposalNeedsScan(P.cleanProposal({ version: 1, status: 'rejected', reason: 'owned_cards_unknown' })),
         P.noProposalNeedsScan(P.cleanProposal({ version: 1, status: 'rejected', fallback_reason: 'card_db_empty' }))],
}));
""")
        self.assertEqual(out["never"], {"ok": False, "kind": "never", "text": "需扫描持有卡"})
        self.assertEqual((out["onlyP"], out["few"], out["ready"], out["notOwnedP"]), ("incomplete", "incomplete", True, "incomplete"))
        self.assertEqual(out["why"], "Vo 约 663，SP 约 322（技能计划需 280），50 万粉丝路线可行；评分 76.7（按 32 周成长估算）")
        self.assertEqual(out["outdated"], [False, True])
        self.assertEqual(out["scan"], [True, False])


if __name__ == "__main__":
    unittest.main()
