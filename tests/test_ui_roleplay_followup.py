"""2026-10-26 角色扮演评审的低影响收尾项：主页新手步骤与措辞、选卡器空状态与标签、
礼物箱结果说明、点击区与拖动手柄、设置首次展开项、资源图标「不使用」与读数中文、训练卡未校准/不在持有卡牌、
编队核对预览与估算标记、剧本芯片「未开放」、停止提示。Node + tests/fixtures/ui/fake_dom.mjs；无浏览器、无游戏输入。"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"

PRELUDE = (
    "import './tests/fixtures/ui/fake_dom.mjs';"
    "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
    "const store = {}; globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };"
    "const all = (node, out = []) => { for (const c of node.childNodes || []) { if (c.tagName) { out.push(c); all(c, out); } } return out; };"
)


def read(path):
    return (UI / path).read_text(encoding="utf-8")


def run_node(body, *args):
    result = subprocess.run(["node", "--input-type=module", "-e", PRELUDE + body, *args], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class HomeFollowupTests(unittest.TestCase):
    def test_first_run_steps_are_numbered_with_a_done_state_from_existing_data(self):
        out = run_node(
            "const { firstRunSteps, firstRunView } = await import('./ui/components/home-panel.js');"
            "const home = [{ tasks: [{ name: 'Startup', status: 'succeeded' }, { name: 'ResourceRead', status: 'succeeded' },"
            " { name: 'Shop', status: 'skipped' }] }];"
            "const base = { runs: home, run: { active: false }, host: { connected: true },"
            " profile: { tasks: [{ name: 'Training', enabled: true, option: {} }], removed_tasks: [] } };"
            "const fresh = { ...base, runs: [] };"
            "const keys = (s) => s.map((x) => `${x.key}:${x.done ? 1 : 0}`);"
            "const chosen = { ...base, profile: { tasks: [{ name: 'Training', enabled: true, option: { 训练卡: '2040010010' } }], removed_tasks: [] } };"
            "const calls = [];"
            "const view = firstRunView(firstRunSteps(base), { openTask: (n) => calls.push(n) });"
            "const els = all(view);"
            "const button = els.find((e) => e.tagName === 'BUTTON');"
            "button.listeners.click[0]();"
            "const runs = [];"
            "const freshButton = all(firstRunView(firstRunSteps(fresh), { startRun: (n) => runs.push(n) })).find((e) => e.tagName === 'BUTTON');"
            "freshButton.listeners.click[0]();"
            "console.log(JSON.stringify({"
            " ready: keys(firstRunSteps(base)),"
            " titlePage: keys(firstRunSteps(fresh)),"
            " failed: keys(firstRunSteps({ ...base, runs: [{ tasks: [{ name: 'Startup', status: 'failed' }] }] })),"
            " guard: keys(firstRunSteps({ ...base, host: { connected: true, host_guard_reason: 'login_required' } })),"
            " offline: keys(firstRunSteps({ ...base, host: { connected: false } })),"
            " chosen: keys(firstRunSteps(chosen)),"
            " noTraining: keys(firstRunSteps({ ...base, profile: { tasks: [], removed_tasks: [] } })),"
            " afterRun: firstRunSteps({ ...base, runs: [...home, { tasks: [{ name: 'Startup', status: 'succeeded' }, { name: 'Shop', status: 'failed' }] }] }).length,"
            " text: view.textContent, calls, runs, freshLabel: freshButton.textContent,"
            " nums: els.filter((e) => /first-run-num/.test(e.attributes.class || '')).map((e) => e.textContent),"
            " steps: els.filter((e) => e.tagName === 'LI').map((e) => /is-done/.test(e.attributes.class || '')),"
            " buttons: els.filter((e) => e.tagName === 'BUTTON').map((e) => e.textContent),"
            " chosenButtons: all(firstRunView(firstRunSteps(chosen), {})).filter((e) => e.tagName === 'BUTTON').length }));")
        self.assertEqual(out["ready"], ["login:1", "training:0", "start:0"])   # 启动 reached the home page
        self.assertEqual(out["titlePage"][0], "login:0")                                     # connected on the title page: not yet
        self.assertEqual(out["failed"][0], "login:0")
        self.assertEqual(out["guard"][0], "login:0")
        self.assertEqual(out["offline"][0], "login:0")
        self.assertEqual(out["chosen"], ["login:1", "training:1", "start:0"])  # a chosen card stays listed, done
        self.assertEqual([k.split(":")[0] for k in out["noTraining"]], ["login", "start"])
        self.assertEqual(out["afterRun"], 0)                                                  # a daily task ran: the guide is over
        self.assertTrue(out["text"].startswith("第一次使用1/3"))                             # progress, not a problem count
        self.assertEqual(out["nums"], ["", "2", "3"])                                    # numbered; the done step shows a check
        self.assertEqual(out["steps"], [True, False, False])
        self.assertEqual((out["buttons"], out["calls"]), (["去任务"], ["Training"]))
        self.assertEqual((out["freshLabel"], out["runs"]), ("检查", ["ResourceRead"]))     # 检查 = 只读取资源
        self.assertEqual(out["chosenButtons"], 0)                                             # nothing left to do for a done step
        home = read("components/home-panel.js")
        self.assertNotIn('title: "第一次使用", label: "第一次使用"', home)                  # no longer the orange attention card
        self.assertNotIn("首次教程", home)                                                 # Maa closes first-visit help (2026-11-23)

    def test_first_run_tip_asks_for_card_details_until_they_are_on_this_pc(self):
        out = run_node(
            "const { firstRunTip, firstRunView } = await import('./ui/components/home-panel.js');"
            "const lib = (card_library) => ({ host: { card_library } });"
            "const missing = firstRunTip(lib({ supported: true, state: 'idle' }));"
            "const busy = firstRunTip(lib({ supported: true, state: 'running', phase: 'cards', updated_cards: 400, total_cards: 1522 }));"
            "const ready = firstRunTip(lib({ supported: true, state: 'done', last: { cards_complete: true, local_cards: 1522 } }));"
            "const calls = [];"
            "const els = all(firstRunView([{ key: 'start', text: 'x', done: false }], { cardLibraryUpdate: () => calls.push(1) }, missing));"
            "els.find((e) => e.dataset?.focusKey === 'first-card-details').listeners.click[0]();"
            "const busyButtons = all(firstRunView([], {}, busy)).filter((e) => e.tagName === 'BUTTON').length;"
            "console.log(JSON.stringify({ missing, busy, ready, calls, busyButtons,"
            " unsupported: firstRunTip(lib({ supported: false })) }));")
        self.assertEqual(out["missing"], {"text": "自动挖矿需要卡资料，首次约 45 分钟", "busy": False})
        self.assertEqual(out["busy"]["text"], "卡资料下载中 400 / 1522 · 约 33 分钟")
        self.assertEqual((out["ready"], out["unsupported"]), (None, None))                # on this PC: no tip
        self.assertEqual((out["calls"], out["busyButtons"]), ([1], 0))

    def test_reset_countdown_says_refresh_and_last_run_names_where_it_stopped(self):
        out = run_node(
            "const { resetText, stoppedAt } = await import('./ui/components/daily-panel.js');"
            "const t = (name, status) => ({ name, status });"
            "console.log(JSON.stringify({"
            " text: resetText(new Date('2026-10-07T01:48:00+09:00'), new Date('2026-10-07T05:00:00+09:00'), 5),"
            " uncertain: stoppedAt({ tasks: [t('Startup', 'succeeded'), t('Shop', 'uncertain'), t('Training', 'pending')] }),"
            " stopped: stoppedAt({ outcome: 'stopped', tasks: [t('Startup', 'succeeded'), t('Shop', 'skipped'), { name: 'Training' }] }),"
            " takeover: stoppedAt({ outcome: 'stopped', tasks: [t('Startup', 'succeeded'), t('Business', 'takeover')] }),"
            " refused: stoppedAt({ outcome: 'refused', tasks: [t('Startup', 'succeeded'), t('Shop', 'guard_refused')] }),"
            " strings: stoppedAt({ outcome: 'stopped', tasks: ['Startup', 'Shop'] }),"
            " ok: stoppedAt({ outcome: 'succeeded', tasks: [t('Startup', 'succeeded')] }),"
            " skippedOnly: stoppedAt({ tasks: [t('Startup', 'skipped')] }), none: stoppedAt(null) }));")
        self.assertEqual(out["text"], "3 小时 12 分")                                # clock glyph + countdown; the reset time is the tooltip
        self.assertNotIn("重置", out["text"])                       # 「重置」 is the 任务 tab's restore-the-plan button
        self.assertEqual(out["uncertain"], {"name": "Shop", "status": "uncertain"})
        self.assertEqual(out["stopped"], {"name": "Training", "status": "pending"})   # never started: the run stopped before it
        self.assertEqual(out["takeover"], {"name": "Business", "status": "takeover"})
        self.assertEqual(out["refused"], {"name": "Shop", "status": "guard_refused"})
        self.assertEqual(out["strings"], {"name": "Startup", "status": "pending"})
        self.assertIsNone(out["ok"])
        self.assertIsNone(out["skippedOnly"])
        self.assertIsNone(out["none"])
        home = read("components/home-panel.js")
        self.assertIn("resetText(now, next)", home)
        self.assertNotIn("重置 ${", home)
        self.assertIn("停在 ${taskName(state, stopped.name)}", home)
        self.assertIn("下次刷新：", home)
        passive = read("components/passive-readings.js")
        self.assertIn("（周日 05:00 刷新）", passive)

    def test_repeated_placeholders_are_quiet(self):
        home = read("components/home-panel.js")
        # ticked-but-not-run tasks get an empty ring (the 「未运行」 text stays as its label and tooltip); 未勾选 keeps its word
        self.assertIn(': planned ? "未运行" : "未勾选"', home)
        self.assertIn("home-check-ring", home)
        self.assertEqual(home.count('"尚未读取"'), 0)                         # the strips show 「—」; 尚未读取 is the tooltip / header
        self.assertIn("尚未读取：运行日常后读取本周完成数", home)
        css = read("styles/pages.css")
        self.assertIn(".home-check-ring", css)
        self.assertIn(".first-run-num", css)

    def test_stop_buttons_say_when_they_stop(self):
        for page in ("task-panel.js", "wing-panel.js", "live-panel.js"):
            self.assertIn("当前步骤结束后停下", read(f"components/{page}"), page)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PickerFollowupTests(unittest.TestCase):
    def test_training_card_warnings_and_flag(self):
        out = run_node(
            "const T = await import('./ui/components/team-picker.js');"
            "const calibrated = { calibrated: true }; const raw = { calibrated: false };"
            "const keys = (v, o) => T.trainingCardWarnings(v, o).map((w) => w.key);"
            "const entry = T.cardEntry({ id: '2040010010', idol: '月岡 恋鐘', title: 'A', rarity: 'SR', specialty: 'Vocal' }, { flag: '未校准', flagTip: 'tip' });"
            "console.log(JSON.stringify({"
            " none: keys('', { art: raw, owned: [] }),"
            " raw: keys('2040010010', { art: raw, owned: ['2040010010'] }),"
            " ok: keys('2050170010', { art: calibrated, owned: ['2050170010'] }),"
            " left: keys('2050170010', { art: calibrated, owned: ['2040010010'] }),"
            " both: keys('2040010010', { art: raw, owned: ['2050170010'] }),"
            " emptyOwned: keys('2040010010', { art: calibrated, owned: [] }),"
            " noList: keys('2040010010', { art: null, owned: ['2050170010'] }),"
            " filters: T.cardFilters([{ id: '2040010010', rarity: 'SSR', idol: '月岡 恋鐘', specialty: 'Vocal' }]).map((f) => f.label),"
            " flag: entry.flag, tipHasFlag: entry.tip.includes('tip'), tags: entry.tags }));")
        self.assertEqual(out["none"], [])
        self.assertEqual(out["raw"], ["uncalibrated"])
        self.assertEqual(out["ok"], [])
        self.assertEqual(out["left"], ["unowned"])
        self.assertEqual(out["both"], ["uncalibrated", "unowned"])
        self.assertEqual(out["emptyOwned"], [])                  # an empty 持有卡牌 has its own 「去扫描」 hint
        self.assertEqual(out["noList"], ["unowned"])             # nothing is known about calibration without the card list
        self.assertEqual(out["filters"], ["稀有度", "偶像", "特长"])
        self.assertEqual((out["flag"], out["tipHasFlag"]), ("未校准", True))

    def test_empty_card_list_points_into_the_app_and_filters_are_labelled(self):
        picker = read("components/team-picker.js")
        self.assertNotIn("tools/", picker)
        self.assertNotIn(".py", picker)                          # no command lines in the player view
        self.assertIn("还没有卡牌资料：先在「资源」页的「卡资料」下载", picker)
        self.assertIn("去资源页下载", picker)
        self.assertIn("goResources", picker)
        self.assertIn("filters: all.length ? cardFilters(all) : []", picker)   # no empty filter rows without a card list
        grid = read("components/card-picker.js")
        self.assertIn('class: "card-filter-label", text: f.label', grid)       # each filter row names itself
        self.assertIn("gridNote", grid)
        self.assertIn("card-tag is-flag", grid)
        css = read("styles/team.css")
        self.assertIn(".card-filter-label", css)
        self.assertIn(".card-tag.is-flag", css)
        field = read("components/team-field.js")
        self.assertEqual(field.count("goResources: goResources()"), 3)         # P, S and the owned-cards picker

    def test_training_row_flags_a_card_that_left_the_owned_list(self):
        panel = read("components/task-panel.js")
        self.assertIn('if (warning.key === "unowned") out.push(', panel)       # under the row, never blocks 开始
        field = read("components/option-field.js")
        self.assertIn('if (warning.key === "uncalibrated") notes.push(', field)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TeamFollowupTests(unittest.TestCase):
    def test_verify_text_and_estimate_tag(self):
        out = run_node(
            "const F = await import('./ui/components/team-field.js');"
            "const P = await import('./ui/components/team-proposal.js');"
            "const snap = { unit: 7, read_at: '2026-10-24T20:00:00+09:00' };"
            "console.log(JSON.stringify({"
            " on: F.verifyText(snap), off: F.verifyText(snap, { check: false }), undated: F.verifyText({ unit: 3, read_at: '' }),"
            " plain: P.estimateTag({ status: 'ok' }), none: P.estimateTag(null),"
            " lb4: P.estimateTag({ status: 'needs_confirmation', assume: { lb4_cap: true } }),"
            " both: P.estimateTag({ status: 'needs_confirmation', assume: { lb4_cap: true, p_te_eligible: true } }),"
            " bare: P.estimateTag({ status: 'needs_confirmation', assume: {} }) }));")
        self.assertEqual(out["on"], "P + S1–S4 + Guest · ユニット7 · 10-24 读取")
        self.assertEqual(out["off"], "本次只核对 P · ユニット7 · 10-24 读取")
        self.assertEqual(out["undated"], "P + S1–S4 + Guest · ユニット3 · 已读取")
        self.assertIsNone(out["plain"])
        self.assertIsNone(out["none"])
        self.assertEqual(out["lb4"], "估算 · 未填凸数按 4 凸")
        self.assertEqual(out["both"], "估算 · 未填凸数按 4 凸 · TE 资格按有")
        self.assertEqual(out["bare"], "估算")

    def test_recommendation_row_shows_the_estimate_and_links_to_the_level_import(self):
        snap = {"version": 1, "unit": 1, "p_card": "1040010010", "s_cards": ["2010010010", "2020020010", "2030030010", "2060060010"],
                "guest": "2070070010", "read_at": "2026-10-10T20:00:00+11:00"}
        rec = {"version": 1, "status": "needs_confirmation", "primary": "Vo", "requested_p_card": snap["p_card"], "requested_primary": "auto",
               "assume": {"lb4_cap": True},
               "proposal": {"p_card": snap["p_card"], "s_cards": ["2010010020", "2020020020", "2030030020", "2060060020"], "guest": "2070070020"}}
        script = (
            "import { teamField } from './ui/components/team-field.js';"
            "import { setTeamSnapshot } from './ui/components/team-snapshot.js';"
            "import { setTeamProposal } from './ui/components/team-proposal.js';"
            "import { readFileSync } from 'node:fs';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "const [snap, rec] = [JSON.parse(process.argv[1]), JSON.parse(process.argv[2])];"
            "const task = schema.tasks.find((t) => t.name === 'AutoProduce');"
            "const keys = task.options.filter((k) => annotations.options[k]?.group === 'Produce编队');"
            "const pp = rec.proposal;"
            "const owned = { p: [snap.p_card], s: [...snap.s_cards, snap.guest, ...pp.s_cards, pp.guest] };"
            "setTeamSnapshot(snap); setTeamProposal(rec);"
            "const tabs = [];"
            "const render = async (option, actions) => {"
            "  const state = { schema, annotations, bridgeKind: 'mock', prefs: { owned_cards: owned }, profile: { tasks: [{ name: 'AutoProduce', enabled: true, option }] } };"
            "  const field = teamField({ state, group: 'Produce编队', keys, values: option, disabled: false, onChange: () => {}, focusKey: 'k', actions });"
            "  await Promise.resolve();"
            "  return { text: field.textContent, buttons: all(field).filter((e) => e.tagName === 'BUTTON') };"
            "};"
            "const rec1 = await render({ ProduceP卡: snap.p_card, Produce编队方式: '自动编队', Produce编队槽位: 'unit6' }, { setTab: (t) => tabs.push(t) });"
            "const link = rec1.buttons.find((b) => b.textContent === '去资源页导入 Lv/凸');"
            "link?.listeners.click[0]();"
            "const bare = await render({ ProduceP卡: snap.p_card, Produce编队方式: '自动编队', Produce编队槽位: 'unit6' }, {});"
            "console.log(JSON.stringify({ text: rec1.text, linked: Boolean(link), tabs, bareHasLink: bare.buttons.some((b) => /Lv\\/凸/.test(b.textContent)) }));")
        result = subprocess.run(["node", "--input-type=module", "-e", PRELUDE + script, json.dumps(snap), json.dumps(rec)], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertIn("已采用", out["text"])
        self.assertIn("估算 · 未填凸数按 4 凸", out["text"])       # visible on the row, not only in the tooltip
        self.assertTrue(out["linked"])
        self.assertEqual(out["tabs"], ["resources"])              # the import itself lives on 资源 → 持有卡牌
        self.assertFalse(out["bareHasLink"])                      # no navigation available: no dead link

    def test_scenario_chips_say_unavailable(self):
        panel = read("components/wing-panel.js")
        self.assertIn('h("span", { class: "produce-rail-soon", text: "未开放" })', panel)
        self.assertIn("if (!s.ready) {", panel)                   # 2026-11-04: ready chips are buttons that switch the scenario
        self.assertIn(".produce-rail-soon", read("styles/wing-panel.css"))


class AnnotationFollowupTests(unittest.TestCase):
    def test_gift_box_has_one_user_case_and_no_note(self):
        # 2026-10-27 user: 有东西就直接点一键领取 没有就跳过 -- no scope choice left, nothing to explain.
        annotations = json.loads(read("annotations.json"))["options"]["プレゼント领取"]
        schema = json.loads(read("fixtures/schema.json"))["options"]["プレゼント领取"]
        self.assertEqual([c["name"] for c in schema["cases"]], ["关闭", "开启"])
        self.assertEqual((schema["default_case"], annotations["dev_cases"]), ("开启", ["关闭"]))
        self.assertNotIn("keep_cases", annotations)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_kept_note_picks_the_note_of_the_current_case(self):
        out = run_node(
            "const { keptNote } = await import('./ui/components/option-field.js');"
            "const { readFileSync } = await import('node:fs');"
            "const note = JSON.parse(readFileSync('ui/annotations.json', 'utf8')).options['Produce报酬后OK'];"
            "console.log(JSON.stringify([keptNote(note, 'No'), keptNote(note, 'Other') ?? null]));")
        self.assertIn("由你处理后按 OK", out[0])
        self.assertIsNone(out[1])


class SettingsAndCssFollowupTests(unittest.TestCase):
    def test_settings_first_visit_opens_the_live_group(self):
        panel = read("components/settings-panel.js")
        self.assertRegex(panel, r'key: "settings-live", title: "实机", defaultOpen: true')
        self.assertRegex(panel, r'key: "settings-look", title: "外观", defaultOpen: false')
        self.assertIn('defaultKey: "settings-live"', panel)       # the wide section list starts on the same group
        common = read("components/common.js")
        self.assertIn("items.find((fold) => fold.dataset.fold === defaultKey)", common)
        self.assertLess(common.index("fold.dataset.fold === saved"), common.index("fold.dataset.fold === defaultKey"))   # a saved pick wins

    def test_hit_areas_grip_and_drawer_css(self):
        css = read("styles/app.css")
        self.assertRegex(css, r"\.task-head \.checkbox::before \{[^}]*inset: -12px -4px")        # the 16 px box gets a larger target
        self.assertRegex(css, r"\.task-head \.icon-btn::before \{[^}]*inset: -8px -3px")
        self.assertRegex(css, r"\.attention-row \.btn::before \{[^}]*inset: -5px -3px")
        self.assertRegex(css, r"\.grip, \.grip:disabled \{[^}]*opacity: \.4;")                    # faint at rest, not invisible
        self.assertNotRegex(css, r"\.grip, \.grip:disabled \{[^}]*opacity: 0;")
        self.assertIn(".task-row:hover .grip:not(:disabled), .grip:focus-visible { opacity: 1; }", css)
        wide = read("styles/wide.css")
        self.assertIn(".app[data-wide] .log-drawer { padding-inline: calc(var(--page-pad) - var(--sp-4)); }", wide)   # search box ends at 开始
        self.assertRegex(wide, r"\.app\[data-wide\] \.log-drawer\.is-open \{[^}]*min-height: min\(260px, 42vh\)")
        self.assertRegex(wide, r":has\(> \.task-md\) \{[^}]*min-height: min\(120px, 18vh\)")
        self.assertNotIn("@media", wide)                            # wide.css has no media queries (tiers come from layout-mode.js)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ResourceFollowupTests(unittest.TestCase):
    def test_recovery_items_carry_the_never_used_mark(self):
        out = run_node(
            "const m = await import('./ui/components/resource-panel.js');"
            "const state = { annotations: {}, runReadings: [], readings: [] };"
            "const rows = m.holdingRows(state, m.cleanItemScan(null));"
            "const by = Object.fromEntries(rows.map((r) => [r.id, r]));"
            "const order = m.tileOrder(rows).map((r) => r.id);"
            "const tile = m.holdingTile(by.stamina_drink);"
            "const ticket = m.holdingTile(by.fes_ticket);"
            "console.log(JSON.stringify({ flags: Object.fromEntries(rows.map((r) => [r.id, r.indicatorOnly])), order,"
            " drink: tile.textContent, drinkClass: tile.attributes.class, ticket: ticket.textContent,"
            " bronze: m.holdingTile(by.bronze_ticket).attributes.class, detail: m.holdingDetail(by.fes_ticket) }));")
        flags = out["flags"]
        for item in ("stamina_drink", "fes_ticket", "jewel"):
            self.assertTrue(flags[item], item)
        for item in ("bronze_ticket", "skip_ticket", "money", "fes", "stamina"):
            self.assertFalse(flags[item], item)                       # the counters and spendable tickets are not marked
        self.assertTrue({"stamina_drink", "fes_ticket"} <= set(out["order"][-3:]))   # never-used items sort last
        self.assertIn("不使用", out["drink"])
        self.assertNotIn("）（不使用）", out["drink"])                 # merged into the existing parenthetical
        self.assertIn("フェスエントリーチケット（不使用）", out["ticket"])
        self.assertIn("is-indicator", out["drinkClass"])
        self.assertNotIn("is-indicator", out["bronze"])
        self.assertIn("（不使用）", out["detail"])
        # the policy list and the counters' icons stay as the user asked on 2026-10-08b (体力 → リカバリーソーダ, 参加权 → エントリーチケット)
        self.assertIn('stamina: "stamina_drink", fes: "fes_ticket"', read("components/resource-panel.js"))

    def test_run_readings_are_chinese_and_stale_daily_readings_agree_with_home(self):
        out = run_node(
            "const m = await import('./ui/components/resource-panel.js');"
            "const facts = m.readingFacts({ status: 'succeeded', count: 3, ok: true, price: 100, counter: [3, 7], mystery_key: 5, log_state: 'zzz_unknown',"
            " outcome: 'skipped', observations: [{ money: 1 }], items: {} });"
            "const now = Date.parse('2026-10-26T12:00:00+09:00');"
            "const state = { readings: ["
            " { key: 'missions', label: '任务达成', value: 3, max: 7, observed_at: '2026-10-04T08:12:31+10:00' },"
            " { key: 'mission_progress', label: '任务达成', value: 1, max: 7, observed_at: '2026-10-26T07:00:00+09:00' },"
            " { key: 'business', label: '营业', value: 1, observed_at: '2026-10-04T08:12:31+10:00' },"
            " { key: 'money', item: 'money', label: 'マニー', value: 5, observed_at: '2026-10-04T08:12:31+10:00' }] };"
            "console.log(JSON.stringify({ facts, empty: m.readingFacts({ observations: [], items: {} }),"
            " visible: m.visibleExtraReadings(state, now).map((r) => r.key),"
            " nextDay: m.visibleExtraReadings(state, Date.parse('2026-10-27T12:00:00+09:00')).map((r) => r.key) }));")
        self.assertEqual(out["facts"], "状态 成功 · 数量 3 · 正常 是 · 价格 100 · 计数 3/7 · 结果 跳过")
        self.assertIsNone(re.search(r"[A-Za-z_]{3,}", out["facts"]))   # no English field names
        self.assertEqual(out["empty"], "无数值")
        # 任务达成 counts only on the day it was read (主页 shows 「—」 for an older one): the 22-day-old reading is hidden here too
        self.assertEqual(out["visible"], ["mission_progress", "business"])
        self.assertEqual(out["nextDay"], ["business"])


if __name__ == "__main__":
    unittest.main()
