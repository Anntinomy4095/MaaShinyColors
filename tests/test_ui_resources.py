"""资源 tab redesign (2026-10-08): card status model, 需要你确认 queue, filters, explicit
saves only, summary strip, fold memory. Node + tests/fixtures/ui/fake_dom.mjs; no browser, no game input."""
import json
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
    "const g = await import('./ui/components/game-assets.js');"
    "const c = await import('./ui/components/resource-cards.js');"
    "const m = await import('./ui/components/resource-panel.js');"
    "const common = await import('./ui/components/common.js');"
    "g.useGameAssets({ cards: ["
    " { id: '2040010010', idol: '月岡 恋鐘', title: 'A', rarity: 'SSR', src: 'game-assets/cards/2040010010.png', idea: 'Vocal' },"
    " { id: '2040020010', idol: '田中 摩美々', title: 'B', rarity: 'SR', src: 'game-assets/cards/2040020010.png', idea: 'Dance' },"
    " { id: '2010010010', idol: '櫻木 真乃', title: 'C', rarity: 'SSR', src: 'game-assets/cards/2010010010.png', idea: 'Visual' },"
    " { id: '2010020010', idol: '風野 灯織', title: 'D', rarity: 'R', src: 'game-assets/cards/2010020010.png', idea: 'Vocal' } ],"
    " p_cards: [ { id: '1040010010', idol: '月岡 恋鐘', title: 'P', rarity: 'SSR', src: 'game-assets/cards/1040010010.png', specialty: 'Vocal' } ] });"
    "const find = (node, key) => node?.dataset?.focusKey === key ? node : (node?.childNodes || []).reduce((hit, x) => hit || find(x, key), null);"
    "const click = (node) => { for (const fn of node.listeners.click || []) fn(); };"
    "const data = JSON.parse(process.argv[1] || 'null');"
)

LEVELS = {"cards": [
    {"id": "2040010010", "level": 60, "limitBreak": 4, "stale": False, "date": "2026-10-07", "frame": "a/new.png", "confidence": 0.9},
    {"id": "2040020010", "level": 50, "limitBreak": 3, "stale": True, "date": "2026-10-06", "frame": "a/old.png", "confidence": 0.8},
    {"id": "2010010010", "level": 70, "limitBreak": 2, "stale": False, "date": "2026-10-07", "frame": "a/new.png", "confidence": 0.9},
    {"id": "1040010010", "level": 99, "limitBreak": 4, "stale": False, "date": "2026-10-07", "frame": "a/new.png", "confidence": 0.9},
]}
OWNED = {"p": [], "s": ["2010010010", "2010020010", "2040020010"],
         "details": {"2010010010": {"level": 80, "limit_break": 2}, "2010020010": {"level": 40, "limit_break": 1}}}


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ResourceCardsTests(unittest.TestCase):
    def run_node(self, body, data=None):
        result = subprocess.run(["node", "--input-type=module", "-e", PRELUDE + body, json.dumps(data, ensure_ascii=False)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_clearing_last_card_details_survives_the_save_boundary(self):
        out = self.run_node(
            "const t = await import('./ui/components/team-store.js');"
            "const owned = {p:[],s:['2040010010'],details:{'2040010010':{level:60,limit_break:4}}};"
            "const state = {bridgeKind:'mock',prefs:{owned_cards:owned}};"
            "const saved = []; const actions = {setPref:(_key,value)=>saved.push(value)};"
            "const cleared = c.applyCardDecision(owned,'2040010010',{level:null,limit_break:null});"
            "t.saveOwnedCards(state,actions,cleared);"
            "t.saveOwnedCards(state,actions,{p:[],s:['2040010010','2040020010']});"
            "console.log(JSON.stringify(saved));")
        self.assertEqual(out[0], {"p": [], "s": ["2040010010"]})
        # A list-only edit still keeps existing details; only the explicit detail edit clears them.
        self.assertEqual(out[1]["details"]["2040010010"], {"level": 60, "limit_break": 4})

    def test_status_model_queue_and_declined(self):
        out = self.run_node(
            "const cardScan = { cards: [{ id: '2040010010', score: 0.9, frames: 1 }] };"
            "const e = c.cardEntries(data.owned, { cardScan, levelScan: data.levels });"
            "const by = Object.fromEntries(e.map((x) => [x.id, x]));"
            "const sig = c.readingSignature(by['2040010010']);"
            "const declined = c.cardEntries(data.owned, { cardScan, levelScan: data.levels, declined: { '2040010010': sig, '2010010010': c.readingSignature(by['2010010010']) } });"
            "console.log(JSON.stringify({ status: Object.fromEntries(e.map((x) => [x.id, x.status])),"
            " shown: by['2040010010'].shown, pLevel: by['1040010010'].shown.level, counts: c.statusCounts(e.filter((x) => x.kind === 's')),"
            " queue: c.queueOf(e).map((x) => x.id), declinedIds: declined.map((x) => x.id).sort(),"
            " declinedStatus: Object.fromEntries(declined.map((x) => [x.id, x.status])), name: by['2040010010'].name }));",
            {"owned": OWNED, "levels": LEVELS})
        self.assertEqual(out["status"], {
            "2010010010": "conflict",      # entered Lv80 vs recognised Lv70
            "2010020010": "confirmed",     # entered, no reading
            "2040020010": "pending",       # owned, empty values, (old) reading fills them
            "2040010010": "pending",       # recognised, not owned yet
            "1040010010": "pending",       # P card recognised
        })
        self.assertEqual(out["shown"], {"level": 60, "limit_break": 4})
        self.assertIsNone(out["pLevel"])                                   # P cards never get a level
        self.assertEqual(out["counts"], {"all": 4, "pending": 3, "unknown": 0, "confirmed": 1, "auto": 0})
        self.assertEqual(out["queue"][0], "2010010010")                    # conflicts first
        self.assertLess(out["queue"].index("2040010010"), out["queue"].index("2040020010"))  # fresh before old recording
        self.assertNotIn("2040010010", out["declinedIds"])                  # declined unowned suggestion hidden
        self.assertEqual(out["declinedStatus"]["2010010010"], "confirmed")  # 保留当前: entered values stay, no conflict
        self.assertEqual(out["name"], "月岡恋鐘")

    def test_filters_and_sorting(self):
        out = self.run_node(
            "const e = c.cardEntries(data.owned, { levelScan: data.levels });"
            "const ids = (v) => c.filterCards(e, { kind: 's', status: 'all', rarity: [], attr: [], q: '', sort: 'idol', ...v }).map((x) => x.id);"
            "console.log(JSON.stringify({ all: ids({}), pending: ids({ status: 'pending' }), ssr: ids({ rarity: ['SSR'] }),"
            " vo: ids({ attr: ['vo'] }), q: ids({ q: '摩美々' }), qEn: ids({ q: '月岡 恋' }), lv: ids({ sort: 'lv' }), p: c.filterCards(e, { kind: 'p', status: 'all', rarity: [], attr: [] }).map((x) => x.id) }));",
            {"owned": OWNED, "levels": LEVELS})
        self.assertEqual(out["all"], ["2010010010", "2040010010", "2010020010", "2040020010"])  # idol number (digits 4-6), then id
        self.assertEqual(set(out["pending"]), {"2010010010", "2040010010", "2040020010"})       # 待确认 includes conflicts
        self.assertEqual(out["ssr"], ["2010010010", "2040010010"])
        self.assertEqual(out["vo"], ["2040010010", "2010020010"])
        self.assertEqual(out["q"], ["2040020010"])
        self.assertEqual(out["qEn"], ["2040010010"])
        self.assertEqual(out["lv"][0], "2010010010")    # Lv70 (reading shown while unconfirmed) > Lv60 > Lv50 > Lv40
        self.assertEqual(out["p"], ["1040010010"])

    def test_decisions_are_explicit_and_go_through_one_callback(self):
        out = self.run_node(
            "const commits = [];"
            "localStorage.setItem('sc-ui-resources-cards-v2', JSON.stringify({ kind: 's' }));"   # P comes first by default; this test acts on S
            "const state = { bridgeKind: 'mock', prefs: { owned_cards: data.owned }, annotations: {}, runReadings: [], readings: [] };"
            "const holdings = { itemScan: m.cleanItemScan(null), cardScan: { cards: [{ id: '2040010010', score: 0.9, frames: 1 }] }, levelScan: data.levels };"
            "const view = m.holdingsView(state, holdings, { onCommit: (next) => commits.push(next) });"
            "const before = commits.length;"
            "click(find(view, 'rq-ok-2010010010'));"   # conflict row: 「用识别值」
            "const add = c.applyCardDecision(data.owned, '1040010010', { own: true, level: 99, limit_break: 4 });"
            "const drop = c.applyCardDecision(data.owned, '2010020010', { own: false });"
            "const bad = c.applyCardDecision(data.owned, '2040010010', { own: true, level: 500, limit_break: 9 });"
            "console.log(JSON.stringify({ before, commits, add, drop, bad, text: view.textContent }));",
            {"owned": OWNED, "levels": LEVELS})
        self.assertEqual(out["before"], 0)                                    # rendering never saves
        self.assertEqual(len(out["commits"]), 1)
        self.assertEqual(out["commits"][0]["details"]["2010010010"], {"level": 70, "limit_break": 2})
        self.assertEqual(out["commits"][0]["details"]["2010020010"], {"level": 40, "limit_break": 1})  # others untouched
        self.assertEqual(out["add"]["p"], ["1040010010"])
        self.assertEqual(out["add"]["details"]["1040010010"], {"limit_break": 4})                       # no level for P
        self.assertNotIn("2010020010", out["drop"]["s"])
        self.assertNotIn("2010020010", out["drop"]["details"])
        self.assertIn("2040010010", out["bad"]["s"])
        self.assertNotIn("2040010010", out["bad"].get("details", {}))                                   # invalid values dropped
        for word in ("用识别值", "修改", "导入持有卡牌", "导入 Lv/凸", "待确认", "已确认", "未识别"):
            self.assertIn(word, out["text"])
        self.assertNotIn("null", out["text"])

    def test_summary_strip_freshness_and_compact_numbers(self):
        out = self.run_node(
            "const now = Date.parse('2026-10-08T12:00:00+11:00');"
            "const scan = m.cleanItemScan({ version: 1, items: [{ id: 'money', count: 100, observed_at: '2026-10-05T12:00:00+11:00', frame: 'x/f.png' }] });"
            "const state = { annotations: {}, runReadings: [], readings: [{ key: 'money', item: 'money', label: 'マニー', value: 250, observed_at: '2026-10-08T11:00:00+11:00', source: 'r' },"
            " { key: 'missions', label: '任务达成', value: 3, max: 7, observed_at: '2026-10-08T11:30:00+11:00' }] };"
            "const values = m.summaryValues(state, m.holdingRows(state, scan));"
            "const view = m.holdingsView({ ...state, prefs: { owned_cards: { p: [], s: [] } }, bridgeKind: 'mock' }, { itemScan: scan, cardScan: { cards: [] } }, { now });"
            "console.log(JSON.stringify({ ids: values.map((v) => v.id), money: values[0].count, stamina: values[2].count,"
            " fresh: c.freshness('2026-10-05T12:00:00+11:00', now), recent: c.freshness('2026-10-08T11:00:00+11:00', now),"
            " compact: [m.compactNumber(9999), m.compactNumber(12345), m.compactNumber(17693906), m.compactNumber(350000000)], text: view.textContent }));")
        self.assertEqual(out["ids"], ["money", "fes", "stamina", "jewel"])   # 2026-10-08b: ジュエル replaces 铜训练券 (display only)
        self.assertEqual(out["money"], 250)            # newer state.readings value wins over the older scan
        self.assertIsNone(out["stamina"])              # never 0
        self.assertTrue(out["fresh"]["stale"])
        self.assertEqual(out["fresh"]["text"], "3 天前")
        self.assertFalse(out["recent"]["stale"])
        self.assertEqual(out["compact"], ["9,999", "1.2万", "1769万", "3.5亿"])
        self.assertIn("任务达成", out["text"])          # non-holding readings stay visible under the strip
        self.assertIn("尚未读取", out["text"])

    # ---------------------------------------------------------------- auto-apply (b)
    AUTO_LEVELS = {"generatedAt": "2026-10-08T10:00:00+1100", "cards": [
        {"id": "2040010010", "level": 60, "limitBreak": 4, "stale": False, "date": "2026-10-08", "frame": "a/new.png", "confidence": 0.97},  # unowned, high
        {"id": "2040020010", "level": 50, "limitBreak": 3, "stale": False, "date": "2026-10-08", "frame": "a/new.png", "confidence": 0.62},  # owned empty, low
        {"id": "2010010010", "level": 70, "limitBreak": 2, "stale": False, "date": "2026-10-08", "frame": "a/new.png", "confidence": 0.99},  # manual Lv80: conflict
        {"id": "2010020010", "level": 45, "limitBreak": 1, "stale": True, "date": "2026-10-06", "frame": "a/old.png", "confidence": 0.92},   # stale: 0.89 -> low
        {"id": "1040010010", "level": None, "limitBreak": 4, "stale": False, "date": "2026-10-08", "frame": "a/new.png"},                    # no confidence -> low
    ]}
    AUTO_OWNED = {"p": [], "s": ["2010010010", "2010020010", "2040020010"],
                  "details": {"2010010010": {"level": 80, "limit_break": 2}, "2010020010": {"level": 40, "limit_break": 1}}}

    def test_auto_apply_only_high_never_over_manual_and_idempotent(self):
        out = self.run_node(
            "const scans = { levelScan: data.levels, cardScan: { generatedAt: '', cards: [{ id: '2040030010', score: 0.95, frames: 1, confidence: 0.95 },"
            " { id: '2040040010', score: 0.95, frames: 1 }] } };"
            "const plan = c.autoApplyPlan(data.owned, scans, c.loadAuto(), { now: Date.parse('2026-10-08T12:00:00Z') });"
            "const again = c.autoApplyPlan(plan.next, scans, plan.store);"
            "const entries = c.cardEntries(plan.next, { ...scans, auto: plan.store.cards });"
            "console.log(JSON.stringify({ ids: plan.ids, next: plan.next, again: again.changed, againSame: again.store === plan.store,"
            " rec: plan.store.cards['2040010010'], status: Object.fromEntries(entries.map((e) => [e.id, e.status])),"
            " conf: Object.fromEntries(entries.map((e) => [e.id, e.confidence])), batch: c.lastBatch(plan.next, plan.store), min: c.AUTO_MIN }));",
            {"owned": self.AUTO_OWNED, "levels": self.AUTO_LEVELS})
        self.assertEqual(out["min"], 0.7)  # 「一定限度就可以maa自己确认」
        self.assertEqual(out["ids"], ["2040010010", "2040030010"])           # high only: Lv/凸 reading + ownership-only scan
        self.assertIn("2040010010", out["next"]["s"])
        self.assertEqual(out["next"]["details"]["2040010010"], {"level": 60, "limit_break": 4})
        self.assertEqual(out["next"]["details"]["2010010010"], {"level": 80, "limit_break": 2})   # user-set Lv80 kept
        self.assertEqual(out["next"]["details"]["2010020010"], {"level": 40, "limit_break": 1})   # stale (0.92-0.03) is low
        self.assertNotIn("2040020010", out["next"].get("details", {}))                             # low: waits for the user
        self.assertNotIn("1040010010", out["next"]["p"])                                          # missing confidence = low
        self.assertNotIn("2040040010", out["next"]["s"])                                          # old scan without confidence
        self.assertFalse(out["again"])                                                             # once per scan file
        self.assertTrue(out["againSame"])
        self.assertEqual(out["rec"]["source"], "auto")
        self.assertEqual(out["rec"]["confidence"], 0.97)
        self.assertEqual(out["rec"]["prev"], {"owned": False})
        self.assertTrue(out["rec"]["at"].startswith("2026-10-08T12:00"))
        self.assertTrue(out["rec"]["scan"])
        self.assertEqual(out["status"]["2040010010"], "auto")
        self.assertEqual(out["status"]["2010010010"], "conflict")
        self.assertEqual(out["status"]["2040020010"], "pending")
        self.assertEqual(out["status"]["1040010010"], "pending")
        self.assertAlmostEqual(out["conf"]["2010020010"], 0.89)
        self.assertEqual(out["conf"]["1040010010"], 0)
        self.assertEqual(out["batch"], ["2040010010", "2040030010"])

    def test_threshold_change_reruns_and_every_reading_above_min_is_applied(self):
        out = self.run_node(
            "const cards = Array.from({ length: 40 }, (_, i) => ({ id: `20400${String(i + 1).padStart(5, '0')}`.slice(0, 10), level: 50, limitBreak: 2,"
            " stale: false, date: '2026-10-09', frame: 'a/n.png', confidence: 0.7 + i * 0.0075 }));"
            "const levels = { generatedAt: '2026-10-09T10:00:00+11:00', rejected: 392, cards };"
            # a store written when the threshold was 0.85: that scan file was 'handled' with the old key shape
            "const old = { ...c.loadAuto(), scans: ['lv:2026-10-09T10:00:00+11:00|own:'] };"
            "const plan = c.autoApplyPlan({ p: [], s: [] }, { levelScan: levels }, old);"
            "const again = c.autoApplyPlan(plan.next, { levelScan: levels }, plan.store);"
            "const entries = c.cardEntries(plan.next, { levelScan: levels, auto: plan.store.cards });"
            "console.log(JSON.stringify({ n: plan.ids.length, again: again.changed, key: c.scanKey({ levelScan: levels }),"
            " statuses: [...new Set(entries.map((e) => e.status))], queue: c.queueOf(entries).length, rejected: c.unrecognisedTiles({ levelScan: levels }) }));",
        )
        self.assertEqual(out["n"], 40)                       # every reading >= 0.70 applied, also under an old-threshold store
        self.assertFalse(out["again"])
        self.assertIn("|t:0.7", out["key"])                   # the key carries the threshold
        self.assertEqual(out["statuses"], ["auto"])
        self.assertEqual(out["queue"], 0)
        self.assertEqual(out["rejected"], 392)                # unrecognised tiles: a note, not a queue entry

    def test_unrecognised_tiles_note_and_holdings_applied_from_any_page(self):
        out = self.run_node(
            "const prefs = { owned_cards: { p: [], s: [] } }; const saves = [];"
            "const state = () => ({ bridgeKind: 'mock', prefs, annotations: {}, runReadings: [], readings: [] });"
            "const actions = { setPref: (k, v) => { saves.push(v); prefs.owned_cards = v; } };"
            "const holdings = { itemScan: m.cleanItemScan(null), cardScan: { cards: [] }, levelScan: { ...data.levels, rejected: 12 } };"
            "const before = m.needsAutoApply(holdings);"
            "m.autoApplyHoldings(state(), actions, holdings, () => {}, { notify: () => {} });"
            "const view = m.holdingsView(state(), holdings, {});"
            "console.log(JSON.stringify({ before, after: m.needsAutoApply(holdings), text: view.textContent, saves: saves.length }));",
            {"levels": self.AUTO_LEVELS})
        self.assertTrue(out["before"])
        self.assertFalse(out["after"])                         # the home tab loading the files first must still auto-apply once
        self.assertIn("12 格未识别", out["text"])
        self.assertNotIn("待确认 12", out["text"])

    def test_later_scan_updates_auto_values_but_not_user_values(self):
        out = self.run_node(
            "const first = c.autoApplyPlan(data.owned, { levelScan: data.levels }, c.loadAuto());"
            "const newer = { generatedAt: '2026-10-09T10:00:00+1100', cards: data.levels.cards.map((x) => x.id === '2040010010' ? { ...x, level: 65 }"
            " : x.id === '2010010010' ? { ...x, level: 75 } : x) };"
            "const second = c.autoApplyPlan(first.next, { levelScan: newer }, first.store);"
            # the user edits the auto card by hand -> it is user-set from now on
            "const edited = c.applyCardDecision(second.next, '2040010010', { own: true, level: 66, limit_break: 4 });"
            "const forgot = c.forgetAuto(second.store, ['2040010010']);"
            "const third = c.autoApplyPlan(edited, { levelScan: { ...newer, generatedAt: '2026-10-10T10:00:00+1100',"
            " cards: newer.cards.map((x) => x.id === '2040010010' ? { ...x, level: 70 } : x) } }, forgot);"
            "console.log(JSON.stringify({ second: second.next.details, secondIds: second.ids, prev: second.store.cards['2040010010'].prev,"
            " third: third.next.details['2040010010'], thirdIds: third.ids }));",
            {"owned": self.AUTO_OWNED, "levels": self.AUTO_LEVELS})
        self.assertEqual(out["secondIds"], ["2040010010"])
        self.assertEqual(out["second"]["2040010010"], {"level": 65, "limit_break": 4})   # auto value updated by the newer scan
        self.assertEqual(out["second"]["2010010010"], {"level": 80, "limit_break": 2})   # user value never overwritten
        self.assertEqual(out["prev"], {"owned": False})                                   # undo still returns to before the first
        self.assertEqual(out["third"], {"level": 66, "limit_break": 4})                   # hand-edited: user-set, kept
        self.assertEqual(out["thirdIds"], [])

    def test_undo_single_and_batch(self):
        out = self.run_node(
            "const owned = { ...data.owned, details: { ...data.owned.details } }; delete owned.details['2010020010'];"
            "const levels = { ...data.levels, cards: data.levels.cards.map((x) => x.id === '2010020010' ? { ...x, stale: false, confidence: 0.95 } : x) };"
            "const plan = c.autoApplyPlan(owned, { levelScan: levels }, c.loadAuto());"
            "const one = c.undoAuto(plan.next, plan.store, ['2040010010']);"
            "const again = c.autoApplyPlan(one.next, { levelScan: levels }, { ...one.store, scans: [] });"   # even a forced re-run skips it
            "const entries = c.cardEntries(one.next, { levelScan: levels, auto: one.store.cards });"
            "const all = c.undoAuto(plan.next, plan.store, c.lastBatch(plan.next, plan.store));"
            "console.log(JSON.stringify({ applied: plan.ids, one: one.next, oneIds: one.ids, againIds: again.ids,"
            " status: Object.fromEntries(entries.map((e) => [e.id, e.status])), all: all.next, allIds: all.ids }));",
            {"owned": self.AUTO_OWNED, "levels": self.AUTO_LEVELS})
        self.assertEqual(out["applied"], ["2010020010", "2040010010"])
        self.assertNotIn("2040010010", out["one"]["s"])                          # an auto-added card is removed again
        self.assertEqual(out["one"]["details"]["2010020010"], {"level": 45, "limit_break": 1})   # the other stays
        self.assertEqual(out["oneIds"], ["2040010010"])
        self.assertEqual(out["againIds"], [])                                     # an undone reading is not re-applied
        self.assertEqual(out["status"]["2040010010"], "pending")                  # ... it waits in 需要你确认
        self.assertEqual(out["status"]["2010020010"], "auto")
        self.assertEqual(sorted(out["allIds"]), ["2010020010", "2040010010"])
        self.assertNotIn("2010020010", out["all"].get("details", {}))             # filled value removed: back to empty
        self.assertIn("2010020010", out["all"]["s"])                              # ... but the card stays owned

    def test_panel_auto_apply_saves_once_and_marks_user_decisions(self):
        out = self.run_node(
            "localStorage.setItem('sc-ui-resources-cards-v2', JSON.stringify({ kind: 's' }));"
            "const prefs = { owned_cards: data.owned };"
            "const saves = [];"
            "const state = () => ({ bridgeKind: 'mock', prefs, annotations: {}, runReadings: [], readings: [] });"
            "const actions = { setPref: (k, v) => { saves.push(v); prefs.owned_cards = v; } };"
            "const holdings = { itemScan: m.cleanItemScan(null), cardScan: { cards: [] }, levelScan: data.levels };"
            "m.holdingsView(state(), holdings, {});"
            "const rendered = saves.length;"
            "const quiet = { notify: () => {} };"
            "const ids = m.autoApplyHoldings(state(), actions, holdings, () => {}, quiet);"
            "const ids2 = m.autoApplyHoldings(state(), actions, holdings, () => {}, quiet);"
            "const view = m.holdingsView(state(), holdings, { onUndoAuto: (x) => m.undoAutoApplied(state(), actions, x, () => {}) });"
            "const text = view.textContent;"
            "const hasAutoChip = Boolean(find(view, 'rc-st-auto'));"
            "click(find(view, 'rc-auto-undo'));"
            "console.log(JSON.stringify({ rendered, ids, ids2, saves: saves.length, text, after: prefs.owned_cards, hasAutoChip,"
            " changed: m.changedCards({ s: ['1'], details: {} }, { s: ['1'], details: { '1': { level: 2 } } }) }));",
            {"owned": self.AUTO_OWNED, "levels": self.AUTO_LEVELS})
        self.assertEqual(out["rendered"], 0)                                      # rendering never saves
        self.assertEqual(out["ids"], ["2040010010"])
        self.assertEqual(out["ids2"], [])                                         # idempotent: same scan file
        self.assertEqual(out["saves"], 2)                                         # one auto-apply + one undo
        for word in ("已确认", "撤销本次确认", "待确认"):
            self.assertIn(word, out["text"])
        self.assertFalse(out["hasAutoChip"])                                   # auto-confirmed is the same state as 已确认
        self.assertNotIn("2040010010", out["after"]["s"])                         # batch undo restored the list
        self.assertEqual(out["changed"], ["1"])

    def test_p_cards_first(self):
        out = self.run_node(
            "localStorage.setItem('sc-ui-resources-cards', JSON.stringify({ kind: 's' }));"   # the old key no longer decides
            "const state = { bridgeKind: 'mock', prefs: { owned_cards: data.owned }, annotations: {}, runReadings: [], readings: [] };"
            "const view = m.holdingsView(state, { itemScan: m.cleanItemScan(null), cardScan: { cards: [] }, levelScan: data.levels }, {});"
            "const order = []; (function walk(n) { const k = n?.dataset?.focusKey; if (k === 'rc-kind-p' || k === 'rc-kind-s') order.push(k);"
            " (n?.childNodes || []).forEach(walk); })(view);"
            "console.log(JSON.stringify({ order, pSelected: find(view, 'rc-kind-p').getAttribute('aria-pressed'),"
            " sSelected: find(view, 'rc-kind-s').getAttribute('aria-pressed'), text: view.textContent }));",
            {"owned": OWNED, "levels": LEVELS})
        self.assertEqual(out["order"], ["rc-kind-p", "rc-kind-s"])
        self.assertEqual(out["pSelected"], "true")
        self.assertEqual(out["sSelected"], "false")
        self.assertIn("P 1 · S 4", out["text"])

    def test_summary_icons_use_refill_items(self):
        out = self.run_node(
            "g.useGameAssets({ items: { stamina_drink: { src: 'game-assets/items/stamina_drink.png' }, fes_ticket: { src: 'game-assets/items/fes_ticket.png' },"
            " jewel: { src: 'game-assets/items/jewel.png' }, money: { src: 'game-assets/items/money.png' } } });"
            "const keys = ['money', 'fes', 'stamina', 'jewel'].map((id) => m.iconKeyFor(id));"
            "const well = m.resIcon({ annotations: {} }, 'stamina', { size: 32 });"
            "g.useGameAssets({});"
            "const bare = ['fes', 'stamina'].map((id) => m.iconKeyFor(id));"
            "const plate = m.resIcon({ annotations: {} }, 'stamina', { size: 32 });"
            "console.log(JSON.stringify({ keys, bare, wellClass: well.getAttribute('class'), plateClass: plate.getAttribute('class') }));")
        self.assertEqual(out["keys"], ["money", "fes_ticket", "stamina_drink", "jewel"])
        self.assertEqual(out["bare"], ["fes", "stamina"])                        # packaged build: own line icons
        self.assertIn("is-tile", out["wellClass"])
        self.assertIn("is-plate", out["plateClass"])

    def test_threshold_matches_python(self):
        from agent.core import owned_levels as ol

        cards = (UI / "components" / "resource-cards.js").read_text(encoding="utf-8")
        self.assertIn(f"export const AUTO_MIN = {ol.AUTO_ACCEPT_CONFIDENCE};", cards)

    def test_fold_sections_remember_and_build_lazily(self):
        out = self.run_node(
            "let built = 0;"
            "const a = common.foldSection({ key: 'k1', title: 'T', count: 3, children: () => { built += 1; return 'body'; } });"
            "const lazy = built;"
            "a.open = true; for (const fn of a.listeners.toggle) fn();"
            "const b = common.foldSection({ key: 'k1', title: 'T', children: 'x' });"
            "console.log(JSON.stringify({ lazy, built, reopened: b.getAttribute('open') !== null, saved: JSON.parse(localStorage.getItem('sc-ui-fold-open')),"
            " text: a.textContent }));")
        self.assertEqual(out["lazy"], 0)
        self.assertEqual(out["built"], 1)
        self.assertTrue(out["reopened"])
        self.assertEqual(out["saved"], {"k1": True})
        self.assertIn("3", out["text"])

    def test_static_save_paths_and_storage_guards(self):
        panel = (UI / "components" / "resource-panel.js").read_text(encoding="utf-8")
        cards = (UI / "components" / "resource-cards.js").read_text(encoding="utf-8")
        common = (UI / "components" / "common.js").read_text(encoding="utf-8")
        self.assertEqual(panel.count("saveOwnedCards("), 1)                  # the single call in persistOwned
        self.assertEqual(panel.count("saveOwnedCards(state, actions"), 1)
        self.assertNotIn("saveOwnedCards", cards)                             # cards view only calls onCommit
        self.assertNotIn("setPref", cards)
        self.assertNotIn("localStorage", cards)                               # storage only via common.remembered/remember
        self.assertEqual(common.count("globalThis.localStorage?."), 2)
        self.assertEqual(common.count("try {"), common.count("} catch"))
        for source in (panel, cards):
            self.assertNotIn("run.start", source)
            self.assertNotIn("innerHTML", source)


if __name__ == "__main__":
    unittest.main()
