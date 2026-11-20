"""设置 → 关于 → 事务所: hidden switch, per-device prefs and the floating window drawn from the OfficeEggs snapshot.
Node + fake DOM; no browser, no host, no game input."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"

PRELUDE = ("import './tests/fixtures/ui/fake_dom.mjs';"
           "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {} };"
           "const mem = new Map();"
           "globalThis.localStorage = { getItem: (k) => mem.has(k) ? mem.get(k) : null, setItem: (k, v) => mem.set(k, String(v)),"
           " removeItem: (k) => mem.delete(k) };"
           "const v = await import('./ui/components/office-eggs-view.js');"
           "const find = (node, pred, out = []) => { for (const c of node.childNodes || []) { if (pred(c)) out.push(c); find(c, pred, out); } return out; };")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class OfficeEggsViewTests(unittest.TestCase):
    def run_node(self, body):
        result = subprocess.run(["node", "--input-type=module", "-e", PRELUDE + body], cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_prefs_default_locked_and_round_trip(self):
        out = self.run_node(
            "const first = v.loadEggPrefs();"
            "v.saveEggPrefs({ unlocked: true, on: true, pos: { x: 40, y: 50 } });"
            "const saved = v.loadEggPrefs();"
            "localStorage.setItem(v.PREFS_KEY, JSON.stringify({ unlocked: false, on: true, pos: { x: 'a' } }));"
            "const lockedOn = v.loadEggPrefs();"
            "localStorage.setItem(v.PREFS_KEY, '{broken');"
            "console.log(JSON.stringify({ first, saved, lockedOn, broken: v.loadEggPrefs() }));")
        self.assertEqual(out["first"], {"unlocked": False, "on": False, "pos": None})
        self.assertEqual(out["saved"], {"unlocked": True, "on": True, "pos": {"x": 40, "y": 50}})
        self.assertEqual(out["lockedOn"], {"unlocked": False, "on": False, "pos": None})
        self.assertEqual(out["broken"], {"unlocked": False, "on": False, "pos": None})

    def test_five_quick_taps_unlock_once(self):
        out = self.run_node(
            "let t = 0, hits = 0; const tap = v.versionTapper(() => { hits += 1; }, { now: () => t });"
            "for (let i = 0; i < 4; i++) { tap(); t += 500; }"
            "const afterFour = hits;"
            "t += 4000; tap();"   # too slow: the count starts over
            "const slow = hits;"
            "for (let i = 0; i < 4; i++) { t += 300; tap(); }"
            "console.log(JSON.stringify({ afterFour, slow, five: hits }));")
        self.assertEqual(out, {"afterFour": 0, "slow": 0, "five": 1})

    def test_switch_row_only_when_unlocked_and_toggles(self):
        out = self.run_node(
            "const calls = []; const actions = { officeEggs: (on) => calls.push(on) };"
            "const hidden = v.eggSwitchRow({ unlocked: false, on: false }, actions);"
            "const row = v.eggSwitchRow({ unlocked: true, on: true }, actions);"
            "const btn = find(row, (c) => c.dataset?.focusKey === 'about-office-eggs')[0];"
            "btn.listeners.click[0]();"
            "console.log(JSON.stringify({ hidden, role: btn.getAttribute('role'), checked: btn.getAttribute('aria-checked'), calls }));")
        self.assertIsNone(out["hidden"])
        self.assertEqual((out["role"], out["checked"], out["calls"]), ("switch", "true", [False]))

    def test_tap_on_a_bird_scatters_elsewhere_feeds_and_position_is_clamped(self):
        out = self.run_node(
            "const snap = { birds: [{ x: 0.5, y: 0.8 }] };"
            "console.log(JSON.stringify({ bird: v.tapIntent(snap, 0.52, 0.76), floor: v.tapIntent(snap, 0.2, 0.85),"
            " empty: v.tapIntent({}, 0.5, 0.8), point: v.stagePoint({ left: 10, top: 20, width: 336, height: 189 }, 178, 114.5),"
            " pos: v.clampPos({ x: 5000, y: -30 }, { w: 1280, h: 720 }), none: v.clampPos(null) }));")
        self.assertEqual((out["bird"], out["floor"], out["empty"]), ("scatter", "feed", "feed"))
        self.assertEqual(out["point"], {"x": 0.5, "y": 0.5})
        self.assertEqual(out["pos"], {"x": 1160, "y": 0})
        self.assertIsNone(out["none"])

    def test_window_draws_the_core_snapshot_and_forwards_taps(self):
        core = (UI / "components/office-eggs-core.js").as_uri()
        out = self.run_node(
            "globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};"
            "const proto = Object.getPrototypeOf(document.createElement('div'));"
            "if (!('style' in proto)) Object.defineProperty(proto, 'style', { get() { return this._style ||= {}; } });"
            "proto.remove ||= function () { const p = this.parentNode; if (p) p.childNodes = p.childNodes.filter((c) => c !== this); this.parentNode = null; };"
            f"const core = await import({json.dumps(core)});"
            "let now = 1000, closed = 0;"
            "const art = { pigeon: { src: 'dove.svg' },"
            " chiyuki: { src: 'chiyuki.png' }, mano: { src: 'mano.png' }, tenka: { src: 'tenka.png' }, amana: { src: 'amana.png' } };"
            "localStorage.setItem(core.OFFICE_EGGS_STORAGE_KEY, JSON.stringify({ version: 2, storyIndex: 3, beat: 1 }));"
            "const w = v.createEggWindow({ onClose: () => { closed += 1; }, loadCore: async () => core,"
            " loadAssets: async () => ({ assets: art }), clock: () => now, random: () => 0.25 });"
            "await w.open();"
            "const cls = (c) => c.getAttribute?.('class') || '';"
            "const win = find(document.body, (c) => cls(c) === 'egg-window')[0];"
            "const stage = find(win, (c) => cls(c).includes('egg-stage'))[0];"
            "const actors = find(stage, (c) => cls(c).includes('egg-actor')).map(cls);"
            "const dots = find(win, (c) => cls(c) === 'egg-dots')[0].childNodes.map(cls);"
            "stage.getBoundingClientRect = () => ({ left: 0, top: 0, width: 336, height: 189 });"
            "stage.listeners.click[0]({ target: stage, clientX: 100, clientY: 165 });"
            "const fed = JSON.parse(localStorage.getItem(core.OFFICE_EGGS_STORAGE_KEY)).feeds;"
            "let prevented = 0; stage.listeners.keydown[0]({ key: ' ', preventDefault: () => { prevented++; } });"
            "const flights = JSON.parse(localStorage.getItem(core.OFFICE_EGGS_STORAGE_KEY)).flights;"
            "const scene = w.eggs.snapshot().story; const text = win.textContent;"
            "const props = find(stage, (c) => cls(c).split(' ').includes('egg-prop')).length;"
            "const close = find(win, (c) => c.dataset?.focusKey === 'office-eggs-close')[0];"
            "close.listeners.click[0]();"
            "w.close();"
            "console.log(JSON.stringify({ actors, dots, fed, flights, prevented, scene, text, props, closed, open: w.isOpen,"
            " left: find(document.body, (c) => cls(c) === 'egg-window').length > 0 }));")
        self.assertEqual(len(out["actors"]), 3)   # ALSTROEMERIA beat 2: 甜花、甘奈、千雪
        self.assertTrue(all(actor.get("animation") for actor in out["scene"]["actors"]))
        self.assertTrue(all("pose" not in actor for actor in out["scene"]["actors"]))
        self.assertEqual(out["scene"]["props"], [])
        self.assertEqual(out["props"], 0)
        self.assertEqual(out["text"], "×")
        self.assertEqual(out["dots"].count("on"), 2)
        self.assertEqual((out["fed"], out["flights"], out["prevented"], out["closed"]), (1, 1, 1, 1))
        self.assertFalse(out["open"])
        self.assertFalse(out["left"])

    def test_visitor_waits_visible_time_and_opens_once(self):
        out = self.run_node(
            "const proto = Object.getPrototypeOf(document.createElement('div'));"
            "proto.remove ||= function () { const p = this.parentNode; if (p) p.childNodes = p.childNodes.filter((c) => c !== this); this.parentNode = null; };"
            "const {createOfficeVisitor} = await import('./ui/components/office-eggs-visitor.js');"
            "let now = 0, discovered = 0, clears = 0;"
            "const visitor = createOfficeVisitor({ doc: document, clock: () => now, delayMs: 3000,"
            " setTimer: () => 1, clearTimer: () => { clears++; }, onDiscover: () => { discovered++; } });"
            "visitor.start(); now = 1000; visitor.tick();"
            "document.hidden = true; now = 80000; visitor.tick(); const hidden = visitor.arrived;"
            "document.hidden = false; now = 81000; visitor.tick(); const before = visitor.arrived;"
            "now = 82000; visitor.tick(); const arrived = visitor.arrived;"
            "const btn = find(document.body, c => c.getAttribute?.('aria-label') === '跟着鸽子去事务所')[0];"
            "btn.listeners.click[0](); visitor.arrive();"
            "console.log(JSON.stringify({ hidden, before, arrived, discovered, after: visitor.arrived, clears }));")
        self.assertEqual((out["hidden"], out["before"], out["arrived"]), (False, False, True))
        self.assertEqual((out["discovered"], out["after"]), (1, False))
        self.assertGreater(out["clears"], 0)

    def test_about_has_no_egg_entry_even_with_old_enabled_prefs(self):
        out = self.run_node(
            "const {aboutView} = await import('./ui/components/support-settings.js');"
            "const prefs = { unlocked: true, on: true, pos: { x: 40, y: 50 } };"
            "localStorage.setItem(v.PREFS_KEY, JSON.stringify(prefs));"
            "const saved = localStorage.getItem(v.PREFS_KEY); let hiddenCalls = 0; const supportCalls = [];"
            "const row = aboutView({ schema: { interface: { version: '1.2.3' } }, bridgeKind: 'mock',"
            " supportInfo: { version: '1.2.3', links: ['repo'] }, officeEggs: prefs },"
            " { officeEggsTap: () => { hiddenCalls++; }, officeEggs: () => { hiddenCalls++; },"
            " support: (...args) => supportCalls.push(args) });"
            "const version = find(row, c => c.tagName === 'DD' && c.textContent === '1.2.3')[0];"
            "for (let i = 0; i < 5; i++) for (const click of version.listeners.click || []) click();"
            "const links = find(row, c => c.dataset?.focusKey === 'about-repo'); links[0].listeners.click[0]();"
            "console.log(JSON.stringify({ version: version.textContent, hiddenCalls, supportCalls,"
            " switches: find(row, c => c.getAttribute?.('role') === 'switch').length,"
            " eggEntry: find(row, c => c.dataset?.focusKey === 'about-office-eggs').length,"
            " samePrefs: localStorage.getItem(v.PREFS_KEY) === saved, text: row.textContent }));")
        self.assertEqual(out["version"], "1.2.3")
        self.assertEqual(out["hiddenCalls"], 0)
        self.assertEqual((out["switches"], out["eggEntry"]), (0, 0))
        self.assertTrue(out["samePrefs"])
        self.assertNotIn("事务所", out["text"])
        self.assertEqual(out["supportCalls"], [["open_link", {"link": "repo"}]])


if __name__ == "__main__":
    unittest.main()
