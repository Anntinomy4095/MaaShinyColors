"""主页 first tab, 日志 merged into 任务, per-card gaps in the 自动编队 proposal.

Static checks of the tab whitelist / panel wiring plus node tests of the pure helpers (reset time, attention items,
run outcomes, card-gap cleaning). Offline: no host, no game, no input.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


def read(path):
    return (UI / path).read_text(encoding="utf-8")


def run_node(script, *args):
    result = subprocess.run(["node", "--input-type=module", "-e", script, *args], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class TabLayoutTests(unittest.TestCase):
    def test_tab_whitelist_home_first_and_no_log_or_daily_tab(self):
        header = read("components/header.js")
        block = header[header.index("export const TABS = ["):header.index("];", header.index("export const TABS = ["))]
        tabs = re.findall(r'\{ key: "(\w+)", label: "([^"]+)"(, dev: true)? \}', block)
        self.assertEqual([(k, l, bool(d)) for k, l, d in tabs], [
            ("home", "主页", False), ("tasks", "任务", False), ("produce", "Produce", False), ("resources", "资源", False), ("knowhow", "ノウハウ", False),
            ("settings", "设置", False)])

    def test_app_panels_aliases_and_default_tab(self):
        app = read("app.js")
        tags = app[app.index("const PANEL_TAGS = {"):app.index("};", app.index("const PANEL_TAGS = {"))]
        self.assertEqual(re.findall(r"^\s+(\w+): \"(sc-[\w-]+)\"", tags, re.M), [
            ("live", "sc-live-panel"), ("home", "sc-home-panel"), ("tasks", "sc-task-panel"), ("produce", "sc-wing-panel"), ("resources", "sc-resource-panel"), ("knowhow", "sc-knowhow-panel"),
            ("settings", "sc-settings-panel")])
        self.assertIn('const TAB_ALIASES = { daily: "home", logs: "live", wing: "produce" };', app)
        self.assertIn(': "home",\n    logOpen:', app)
        self.assertIn('store.state = { ...state, activeTab: "home" };', app)
        self.assertIn('import "./components/home-panel.js";', app)
        # 2026-11-19: 日志 / 运行记录 are views of the hidden 「运行中」 panel (no extra tab)
        self.assertIn('import { logAside } from "./components/log-panel.js";', app)
        self.assertIn('openLogs: () => store.set({ activeTab: "live", logView: "logs", logSeenTs: Date.now() }),', app)
        self.assertIn('toggleDrawer: (key) => { if (["logOpen", "historyOpen"].includes(key))', app)

    def test_task_panel_has_a_log_strip_that_opens_the_live_panel_logs(self):
        panel = read("components/task-panel.js")
        self.assertIn('import { logStrip } from "./log-panel.js";', panel)
        # from 1200 px the 运行 column holds the log and 开始 instead
        self.assertIn('this.append(head, planRow, scroll, ...(state.wide === "aside" ? [] : [logStrip(state, actions), dailyRunView(state, actions)]));', panel)
        log = read("components/log-panel.js")
        self.assertNotIn("customElements.define", log)
        for needle in ('"按级别筛选"', '"按任务筛选"', '"搜索日志"', '"运行记录"', 'onclick: () => actions.openLogs() },'):
            self.assertIn(needle, log)
        header = read("components/header.js")
        self.assertNotIn('key: "logs"', header)
        self.assertIn('(!t.live || state.liveTab === true || state.activeTab === "live")', header)
        self.assertIn('{ ...t, label: "日志", icon: "logs" }', header)   # idle: the hidden panel opens as 日志
        live = read("components/live-panel.js")
        self.assertIn('this.append(logViewSwitch(state, actions, view, Boolean(model)));', live)
        self.assertIn('if (panel && panel._view && panel._view !== "run") return true;', live)

    def test_home_panel_is_display_only(self):
        home = read("components/home-panel.js")
        self.assertIn('customElements.define("sc-home-panel", HomePanel);', home)
        # Tab switches and the one 「开始日常」 button, which is exactly the 任务 tab's 开始 (actions.startRun:
        # same blockers, read-only run, host policy); never a bridge request or a spend. 2026-10-12: the manual 免费十连
        # tick is replaced by a read-only 免费ガシャ banner (no action at all).
        for forbidden in ("bridge.", "\"run.start\"", "setOption", "allow_spend", "spend_flags", "setDailyManual"):
            self.assertNotIn(forbidden, home)
        # 2026-10-26: 已核对 (hides a reminder, retries nothing) and 去修改 (opens the task's settings); 2026-10-27: × closes
        # any other line (closeNote: hidden for the day while its text is unchanged)
        self.assertEqual(set(re.findall(r"actions\.(\w+)\(", home)), {"setTab", "startRun", "ackStatus", "openTask", "closeNote"})
        self.assertIn('{ id: "jewel", label: "ジュエル" }', home)
        self.assertNotIn('id: "bronze_ticket"', home)
        self.assertEqual(home.count("actions.startRun()"), 1)
        self.assertIn("disabled: !enabled.length || blockers.length > 0 || live.length > 0", home)   # 2026-10-26: not ready → disabled


@unittest.skipUnless(shutil.which("node"), "node not installed")
class HomeHelpersJsTests(unittest.TestCase):
    def test_gacha_reminder_only_for_a_free_read_of_today(self):
        out = run_node(
            "import { gachaReminder, passiveGacha } from './ui/components/passive-readings.js';"
            "const entry = (value, at) => ({ readings: { 'gacha.home': { value, observed_at: at } } });"
            "const at = '2026-10-12T01:00:00+00:00';"   # 10:00 JST
            "const free = passiveGacha(entry({ state: 'free', kind: 'free_daily' }, at));"
            "const now = new Date('2026-10-12T03:00:00Z');"
            "console.log(JSON.stringify({"
            " today: gachaReminder(free, now),"
            " ten: gachaReminder(passiveGacha(entry({ state: 'free', kind: 'free_ten' }, at)), now)?.tag,"
            " nextDay: gachaReminder(free, new Date('2026-10-12T20:30:00Z')),"   # 05:30 JST next day: new game day
            " plain: gachaReminder(passiveGacha(entry({ state: 'no_tag', kind: null }, at)), now),"
            " unread: passiveGacha(entry({ state: 'unread', kind: null }, at)),"
            " badKind: passiveGacha(entry({ state: 'free', kind: 'paid' }, at)),"
            " none: [gachaReminder(null, now), passiveGacha(null), passiveGacha({ readings: {} })] }));")
        self.assertEqual(out["today"]["tag"], "1日1回無料")
        self.assertEqual(out["ten"], "無料10連")
        self.assertIsNone(out["nextDay"])
        self.assertIsNone(out["plain"])
        self.assertIsNone(out["unread"])
        self.assertIsNone(out["badKind"])
        self.assertEqual(out["none"], [None, None, None])

    def test_week_progress_from_mission_readings(self):
        """2026-10-23: 「本周与进度」 reads the weekly/daily mission pages; only this week's / today's reads count."""
        weekly_rows = [{"title": "フェスアイドルを誕生させよう", "status": "incomplete", "progress": [0, 1]},
                       {"title": "フェスアイドルを3人誕生させよう", "status": "incomplete", "progress": [1, 3]}]
        daily_rows = [{"title": "マニーショップで買い物をしよう", "status": "claimable", "progress": [1, 1]},
                      {"title": "サポートアイドルをトレーニングしよう", "status": "claimed", "progress": [1, 1]},
                      {"title": "デイリーミッションを全てクリアしよう", "status": "incomplete", "progress": None}]
        out = run_node(
            "import { passiveMissions, weeklyProduceFromMissions, dailyMissionSummary, weekCycle, weeklyProduceNudge } from './ui/components/passive-readings.js';"
            "const [weekly, daily] = JSON.parse(process.argv[1]);"
            "const snap = (at, w = weekly) => ({ readings: { 'missions.mission_weekly': { value: { rows: w, rows_visible_only: true }, observed_at: at },"
            " 'missions.mission_daily': { value: { rows: daily, rows_visible_only: true }, observed_at: at } } });"
            "const at = '2026-10-22T13:29:42+00:00';"                 # Thu 22:29 JST
            "const now = new Date('2026-10-23T03:00:00Z');"           # Fri 12:00 JST, same week, next game day
            "const pages = passiveMissions(snap(at));"
            "const claimed = passiveMissions(snap(at, [{ title: 'フェスアイドルを3人誕生させよう', status: 'claimed', progress: null }]));"
            "const unknown = passiveMissions(snap(at, [{ title: 'フェスアイドルを3人誕生させよう', status: 'unknown', progress: null }]));"
            "console.log(JSON.stringify({"
            " cycles: [weekCycle(new Date('2026-10-24T19:59:00Z')), weekCycle(new Date('2026-10-24T20:00:00Z'))],"  # Sun 04:59 / 05:00 JST
            " week: weeklyProduceFromMissions(pages.weekly, now),"
            " nextWeek: weeklyProduceFromMissions(pages.weekly, new Date('2026-10-25T20:00:00Z')),"
            " claimed: weeklyProduceFromMissions(claimed.weekly, now)?.done,"
            " unknown: weeklyProduceFromMissions(unknown.weekly, now),"
            " sameDay: dailyMissionSummary(pages.daily, new Date('2026-10-22T14:00:00Z')),"
            " nextDay: dailyMissionSummary(pages.daily, now),"
            " nudge: [weeklyProduceNudge({ done: 1, target: 3 }, now),"                                  # Fri
            "  weeklyProduceNudge({ done: 1, target: 3 }, new Date('2026-10-24T19:00:00Z')),"          # Sun 04:00 JST = Sat game day
            "  weeklyProduceNudge({ done: 3, target: 3 }, new Date('2026-10-24T03:00:00Z')),"
            "  weeklyProduceNudge({ done: null, target: 3 }, now),"
            "  weeklyProduceNudge({ done: 1, target: 3 }, new Date('2026-10-22T03:00:00Z'))],"         # Thu
            " junk: passiveMissions({ readings: { 'missions.mission_weekly': { value: { rows: 'x' }, observed_at: at } } }) }));",
            json.dumps([weekly_rows, daily_rows], ensure_ascii=False))
        self.assertEqual(out["cycles"], ["W-2026-10-18", "W-2026-10-25"])
        self.assertEqual((out["week"]["done"], out["week"]["target"]), (1, 3))
        self.assertIsNone(out["nextWeek"])
        self.assertEqual(out["claimed"], 3)
        self.assertIsNone(out["unknown"])
        self.assertEqual({k: out["sameDay"][k] for k in ("done", "total", "claimable")}, {"done": 2, "total": 3, "claimable": 1})
        self.assertIsNone(out["nextDay"])
        self.assertIsNone(out["junk"]["weekly"])
        self.assertEqual(out["nudge"], ["本周 Produce 周常还差 2 次（周日 05:00 刷新）"] * 2 + [None, None, None])
        home = read("components/home-panel.js")  # the tiles use these reads; a configured trusted count still wins
        self.assertIn("weeklyProduceFromMissions(pages?.weekly, now)", home)
        self.assertIn("dailyMissionSummary(pages?.daily, now)", home)
        self.assertIn("done: trusted ?? read?.done ?? null", home)

    def test_next_rollover_is_jst_0500(self):
        out = run_node(
            "import { nextRollover, untilText } from './ui/components/daily-panel.js';"
            "const r = (s) => nextRollover(new Date(s)).toISOString();"
            "console.log(JSON.stringify({ before: r('2026-10-07T04:59:00+09:00'), at: r('2026-10-07T05:00:00+09:00'),"
            " after: r('2026-10-07T23:00:00+09:00'), sydney: r('2026-10-07T10:00:00+11:00'),"
            " text: untilText(new Date('2026-10-07T01:48:00+09:00'), new Date('2026-10-07T05:00:00+09:00')) }));")
        self.assertEqual(out["before"], "2026-10-06T20:00:00.000Z")
        self.assertEqual(out["at"], "2026-10-07T20:00:00.000Z")
        self.assertEqual(out["after"], "2026-10-07T20:00:00.000Z")
        self.assertEqual(out["sydney"], "2026-10-07T20:00:00.000Z")
        self.assertEqual(out["text"], "3 小时 12 分")

    def test_attention_items_and_run_outcome(self):
        script = (
            "import { readFileSync } from 'node:fs';"
            "import { profileFromPreset } from './ui/components/store.js';"
            "import { attentionItems, runOutcome, runCounts } from './ui/components/daily-panel.js';"
            "const schema = JSON.parse(readFileSync('ui/fixtures/schema.json', 'utf8'));"
            "const annotations = JSON.parse(readFileSync('ui/annotations.json', 'utf8'));"
            "const demo = JSON.parse(readFileSync('ui/fixtures/demo-state.json', 'utf8'));"
            "const base = { schema, annotations, profile: profileFromPreset(schema, 'daily'), prefs: {}, run: { active: false } };"
            "const busy = { ...base, host: demo.host, runs: demo.runs, ledger: demo.ledger,"
            "  taskStatus: { Shop: { status: 'guard_refused', reason: null }, Startup: { status: 'succeeded' } } };"
            "const calm = { ...base, host: { connected: true }, runs: [], ledger: [], taskStatus: {} };"
            "const off = { ...calm, host: { connected: false } };"
            "console.log(JSON.stringify({ busy: attentionItems(busy, '2026-10-04'), calm: attentionItems(calm, '2026-10-04'),"
            " off: attentionItems(off, '2026-10-04'), outcome: runOutcome(demo.runs[0]), counts: runCounts(demo.runs[0]) }));")
        out = run_node(script)
        keys = [i["key"] for i in out["busy"]]
        self.assertEqual(keys, ["task:Shop", "run:MatchSkip", "ledger", "host"])  # demo host: game input off
        self.assertEqual([i.get("tab") for i in out["busy"]], ["tasks", "tasks", "resources", None])
        self.assertIn("MaaShinyColors.exe", out["busy"][3]["text"])
        self.assertNotIn(".cmd", out["busy"][3]["text"])
        self.assertIn("未执行", out["busy"][0]["text"])
        self.assertIn("2 条消耗记录待核对", out["busy"][2]["text"])
        self.assertEqual(out["calm"], [])
        self.assertEqual(out["off"], [{"key": "host", "tone": "danger", "text": "游戏窗口未连接"}])
        self.assertEqual(out["outcome"], "uncertain")
        self.assertEqual(out["counts"], ["成功 2", "不确定 1", "跳过 1"])

    def test_closed_notes_stay_hidden_until_their_text_changes(self):
        # 「通知需要换成可以清除关闭的」
        script = (
            "import { noteKey, openNotes, loadAcks, saveAcks } from './ui/components/acks.js';"
            "const items = [{ key: 'task:Wing', text: 'W.I.N.G.：失败' }, { key: 'ledger', text: '1 条消耗记录待核对' }];"
            "const state = { acks: { day: '2026-10-27', tasks: [], ledger: [], notes: [noteKey(items[1])] } };"
            "const changed = [items[0], { key: 'ledger', text: '2 条消耗记录待核对' }];"
            "const store = {}; globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };"
            "saveAcks(state.acks); const back = loadAcks(new Date('2026-10-27T03:00:00Z'));"
            "console.log(JSON.stringify({ today: openNotes(state, items, '2026-10-27').map((i) => i.key),"
            " changed: openNotes(state, changed, '2026-10-27').map((i) => i.text), tomorrow: openNotes(state, items, '2026-10-28').length,"
            " kept: back.notes }));")
        out = run_node(script)
        self.assertEqual(out["today"], ["task:Wing"])
        self.assertEqual(out["changed"], ["W.I.N.G.：失败", "2 条消耗记录待核对"])
        self.assertEqual(out["tomorrow"], 2)
        self.assertEqual(out["kept"], ["ledger|1 条消耗记录待核对"])

    def test_card_gaps_are_cleaned_and_summarised(self):
        rows = [
            {"id": "2040010010", "kind": "S", "name": "【a】x", "source": "merged:snapshot-1", "ownership": "ASSUMED_LB4_CAP",
             "ready": {"active": False, "guest": True}, "unknown": {"active": ["owned_live_skills"], "guest": []},
             "unresolved": {"active": ["levels.80 (unresolved)"], "guest": []}, "assumed": ["lb4_cap"], "rejected": "x"},
            {"id": "1040010010", "kind": "P", "name": "p", "source": "db:s", "ownership": "OWNED", "ready": {"p": False},
             "unknown": {"p": ["te_eligible", "<script>"]}, "unresolved": {"p": []}, "assumed": []},
            {"id": "2059990010", "kind": "S", "source": "missing", "ready": {}, "unknown": {}, "unresolved": {}},
            {"id": "bad", "kind": "S"},
        ]
        script = (
            "import { cleanProposal, gapSummary } from './ui/components/team-proposal.js';"
            "const data = { version: 1, card_gaps: JSON.parse(process.argv[1]), assume: { lb4_cap: true, other: true },"
            " gap_counts: { cards: 3, missing: 1, ready_active: 0, ready_guest: 1, ready_p: 0, with_unresolved: 1,"
            " top_unknown: { te_eligible: 1, '<b>': 2 } } };"
            "const c = cleanProposal(data);"
            "console.log(JSON.stringify({ rows: c.cardGaps, counts: c.gapCounts, assume: c.assume,"
            " text: c.cardGaps.map(gapSummary) }));")
        out = run_node(script, json.dumps(rows, ensure_ascii=False))
        self.assertEqual([r["id"] for r in out["rows"]], ["2040010010", "1040010010", "2059990010"])
        self.assertEqual(out["rows"][1]["unknown"]["p"], ["te_eligible"])
        self.assertEqual(out["assume"], {"lb4_cap": True})
        self.assertEqual(out["counts"]["top"], [["te_eligible", 1]])
        self.assertEqual(out["text"][0], "Active 未知 owned_live_skills；冲突 levels.80 (unresolved) · Guest 就绪")
        self.assertEqual(out["text"][1], "P 未知 te_eligible")
        self.assertEqual(out["text"][2], "本地卡库没有这张卡")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class LevelImportJsTests(unittest.TestCase):
    """Reviewed Lv/凸 import: readings with frame/date, stale marking, no silent overwrite, confirm-only save."""

    SCAN = {"version": 1, "generated_at": "2026-10-07T07:08:06+1100", "cards": {
        "2040010010": {"level": 80, "limit_break": 4, "confidence": 0.9, "frame": "owned-levels-20261007/idol-list-lv-001.png", "frames": 1},
        "2040020010": {"level": 70, "limit_break": 3, "confidence": 0.8, "frame": "wing-demo3-20261006-1643-02/frame-000008.png", "frames": 1},
        "2040030010": {"level": 60, "limit_break": 2, "frame": "owned-levels-20261007/idol-list-lv-001.png"},
        "2040040010": {"level": 55, "limit_break": 1, "frame": "owned-levels-20261007/idol-list-lv-001.png"},
        "1040010010": {"level": 99, "limit_break": 4, "frame": "owned-levels-20261007/idol-list-lv-001.png"},
        "2049990010": {"level": 50, "limit_break": 2, "frame": "owned-levels-20261007/idol-list-lv-001.png"},
        "2040050010": {"level": 0, "limit_break": 9, "frame": "../evil"},
        "bad": {"level": 10}}}
    OWNED = {"p": ["1040010010"], "s": ["2040010010", "2040020010", "2040030010", "2040040010"],
             "details": {"2040030010": {"level": 50, "limit_break": 2}, "2040040010": {"level": 55, "limit_break": 1}}}

    def run_import(self, selected=None, use_scan=()):
        script = (
            "import { cleanLevelScan, levelImportRows, applyLevelImport, frameDate } from './ui/components/team-levels-import.js';"
            "const [scan, owned, selected, useScan] = JSON.parse(process.argv[1]);"
            "const clean = cleanLevelScan(scan);"
            "const rows = levelImportRows(owned, clean);"
            "const chosen = new Set(selected ?? rows.filter((r) => r.defaultOn).map((r) => r.id));"
            "console.log(JSON.stringify({ clean, rows, details: applyLevelImport(owned, rows, chosen, new Set(useScan)),"
            " dates: [frameDate('wing-demo3-20261006-1643-02/f.png'), frameDate('owned-levels-20261007/x.png'), frameDate('x.png')] }));")
        return run_node(script, json.dumps([self.SCAN, self.OWNED, selected, list(use_scan)]))

    def test_scan_is_cleaned_with_dates_and_stale_flags(self):
        out = self.run_import()
        cards = {c["id"]: c for c in out["clean"]["cards"]}
        self.assertEqual(set(cards), {"2040010010", "2040020010", "2040030010", "2040040010", "1040010010", "2049990010"})
        self.assertEqual(out["dates"], ["2026-10-06", "2026-10-07", ""])
        self.assertFalse(cards["2040010010"]["stale"])
        self.assertTrue(cards["2040020010"]["stale"])                 # 10-06 recording, older than the 10-07 scan
        self.assertIsNone(cards["1040010010"]["level"])               # P cards have no level
        self.assertEqual(cards["2040010010"]["frame"], "owned-levels-20261007/idol-list-lv-001.png")

    def test_defaults_never_overwrite_and_skip_stale_and_unowned(self):
        out = self.run_import()
        rows = {r["id"]: r for r in out["rows"]}
        self.assertEqual(rows["2040030010"]["conflicts"], ["level"])
        self.assertEqual(rows["2040030010"]["same"], ["limit_break"])
        self.assertEqual(rows["2040040010"]["fill"] + rows["2040040010"]["conflicts"], [])
        self.assertFalse(rows["2040020010"]["defaultOn"])              # stale starts unticked
        self.assertFalse(rows["2049990010"]["owned"])
        self.assertEqual(out["details"], {
            "1040010010": {"limit_break": 4},
            "2040010010": {"level": 80, "limit_break": 4},
            "2040030010": {"level": 50, "limit_break": 2},             # conflict kept (default 保留当前)
            "2040040010": {"level": 55, "limit_break": 1}})

    def test_explicit_choices_apply_only_selected_rows(self):
        out = self.run_import(selected=["2040030010", "2040020010", "2049990010"], use_scan=["2040030010"])
        self.assertEqual(out["details"]["2040030010"], {"level": 60, "limit_break": 2})   # user chose 用识别值
        self.assertEqual(out["details"]["2040020010"], {"level": 70, "limit_break": 3})   # stale, ticked by the user
        self.assertNotIn("2049990010", out["details"])                                     # not owned: never written
        self.assertNotIn("2040010010", out["details"])                                     # unticked

    def test_dialog_requires_confirm_and_button_is_wired(self):
        module = read("components/team-levels-import.js")
        self.assertIn('"确定"', module)
        self.assertIn("if (ok && selected.size) onApply(applyLevelImport(owned, rows, selected, useScan));", module)
        self.assertNotIn("saveOwnedCards", module)
        # 2026-10-11: the 持有卡牌 counts / Lv imports moved from the W.I.N.G. 编队 field to the 资源 page
        field = read("components/team-field.js")
        self.assertNotIn('"导入 Lv/凸"', field)
        self.assertNotIn("openLevelImport", field)
        self.assertIn('"导入 Lv/凸"', read("components/resource-cards.js"))
        self.assertIn("openLevelImport", read("components/resource-panel.js"))



class FlickerTests(unittest.TestCase):
    """2026-10-08: a 1 s status tick (frame age only) must not rebuild the panel or close the 实机未就绪 checklist."""

    def test_host_change_classifies_volatile_ticks(self):
        script = (
            "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
            "const { hostChange } = await import('./ui/components/header.js');"
            "const a = { connected: true, frame_age_ms: 100, capture_seq: 1 };"
            "const tick = { ...a, frame_age_ms: 900, capture_seq: 2 };"
            "console.log(JSON.stringify([hostChange(a, { ...a }), hostChange(a, tick), hostChange(a, { ...a, host_guard_reason: 'x' }),"
            " hostChange(a, { ...a, clicks_this_run: 1, run_window: 2 }),"
            " hostChange(undefined, { connected: false })]));")
        self.assertEqual(run_node(script), ["same", "tick", "change", "tick", "change"])

    def test_click_counters_are_ticks(self):
        """Per-run click counters change during tasks without rebuilding the page each second."""
        script = (
            "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
            "const { hostChange } = await import('./ui/components/header.js');"
            "const a = { connected: true, clicks_this_run: 3, run_window: 1 };"
            "const tick = { ...a, clicks_this_run: 4, run_window: 2 };"
            "console.log(JSON.stringify([hostChange(a, tick), hostChange(a, { ...a, connected: false })]));")
        self.assertEqual(run_node(script), ["tick", "change"])
        self.assertNotIn('state.host?.video?.active === true', read("app.js"))

    def test_tick_render_path_and_details_state_are_kept(self):
        app = read("app.js")
        self.assertIn('change === "tick" ? "status-tick" : "status"', app)
        self.assertIn('if (change === "same") return;', app)
        self.assertIn("const renderTick = () =>", app)
        self.assertIn("renderQueue({ render, renderTick, activity })", app)   # a status tick alone still takes renderTick
        self.assertIn('if (reason !== "status-tick") pending.full = true;', read("components/render-queue.js"))
        # <details> open state survives full renders; the live checklist closes only on outside click / Esc.
        self.assertIn("detailsKeys().forEach(([key, el]) => open.set(key, el.open));", app)
        self.assertIn('document.querySelectorAll("details.live-indicator[open]")', app)
        panel = read("components/task-panel.js")
        self.assertIn('dataset: { openKey: "live-indicator" }', panel)
        self.assertIn("开始前需处理", panel)
        self.assertIn("此处只显示状态", panel)
        bridge = read("components/bridge.js")
        self.assertIn('get("ticks") !== "1"', bridge)


if __name__ == "__main__":
    unittest.main()
