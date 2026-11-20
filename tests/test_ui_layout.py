"""2026-10-25 (user): a wider panel switches to the 双栏 layout instead of stretching buttons; 设置 → 外观 → 布局 offers
自动 / 单栏 / 双栏 (per device). 2026-10-26 工作台: tab rail, section list + section, and a 运行 column from 1200 px.
ui/components/layout-mode.js + ui/styles/wide.css."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"


def read(rel):
    return (UI / rel).read_text(encoding="utf-8")


def run_node(script):
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class LayoutModeTests(unittest.TestCase):
    def test_modes_and_thresholds(self):
        out = run_node(
            "const L = await import('./ui/components/layout-mode.js');"
            "const A = await import('./ui/components/appearance-settings.js');"
            "const cases = [['auto', 879], ['auto', 880], ['single', 2000], ['double', 639], ['double', 640], ['bogus', 900]];"
            "console.log(JSON.stringify({ wide: cases.map(([m, w]) => L.isWide(m, w)), labels: L.LAYOUTS.map(([, t]) => t),"
            " mode: L.layoutMode('pc'), summary: [A.appearanceSummary({}, 'right'), A.appearanceSummary({}, 'right', 'double')] }));")
        self.assertEqual(out["wide"], [False, True, False, False, True, True])
        self.assertEqual(out["labels"], ["自动", "单栏", "双栏"])
        self.assertEqual(out["mode"], "auto")                 # unknown values fall back to 自动
        self.assertEqual(out["summary"], ["跟随系统", "跟随系统 · 双栏"])

    def test_tiers(self):
        out = run_node(
            "const L = await import('./ui/components/layout-mode.js');"
            "const cases = [['auto', 879], ['auto', 880], ['auto', 1199], ['auto', 1200], ['single', 1600], ['double', 700]];"
            "console.log(JSON.stringify({ tiers: cases.map(([m, w]) => L.wideTier(m, w)), min: L.ASIDE_MIN }));")
        self.assertEqual(out["tiers"], [None, "md", "md", "aside", None, "md"])
        self.assertEqual(out["min"], 1200)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PanelSideTests(unittest.TestCase):
    """2026-10-25 (user): the game window and the control UI can be separated — 面板位置「独立窗口」."""

    def test_detached_side(self):
        out = run_node(
            "const A = await import('./ui/components/appearance-settings.js');"
            "console.log(JSON.stringify({ labels: A.PANEL_SIDES.map(([, t]) => t),"
            " sides: ['right', 'left', 'detached', 'bogus', undefined].map((s) => A.panelSide(s)),"
            " parsed: A.windowLayout({ window_layout: { panel_side: 'detached', supported: true } }),"
            " summary: A.appearanceSummary({}, 'detached') }));")
        self.assertEqual(out["labels"], ["右侧", "左侧", "独立窗口"])
        self.assertEqual(out["sides"], ["right", "left", "detached", "right", "right"])
        self.assertEqual(out["parsed"], {"side": "detached", "supported": True})
        self.assertEqual(out["summary"], "跟随系统 · 独立窗口")

    def test_protocol_and_wiring(self):
        protocol = json.loads(read("protocol.json"))
        enum = protocol["commands"]["window.layout"]["params"]["properties"]["panel_side"]["enum"]
        self.assertEqual(enum, ["right", "left", "detached"])
        app = read("app.js")
        self.assertIn("const target = panelSide(side);", app)
        self.assertIn('bridge.request("window.layout", { panel_side: target })', app)
        self.assertIn(".layout-panel:has(> .app-header.is-panel-detached) .side-panel { border-left: 0; }", read("styles/app.css"))


class PanelWindowButtonTests(unittest.TestCase):
    """2026-10-26 (user): a small header button splits the panel into its own window (and puts it back)."""

    def test_header_button(self):
        header = read("components/header.js")
        self.assertIn("export function panelWindowButton(state, actions)", header)
        self.assertIn("if (!layout.supported) return null;", header)            # only where the host can move the panel
        self.assertIn('const label = detached ? "放回游戏窗口" : "拆分到独立窗口";', header)
        self.assertIn("panelWindowButton(state, actions), stop].filter(Boolean));", header)    # beside 紧急停止
        icons, glyphs = read("components/icons.js"), read("components/glyphs.js")
        for name in ("popout", "dockin"):
            self.assertIn(f"  {name}: {{ paths:", icons)
            self.assertIn(f"  {name}: [", glyphs)

    def test_toggle_returns_to_the_docked_side(self):
        app = read("app.js")
        self.assertIn("togglePanelWindow: () => {", app)
        self.assertIn('return actions.windowLayout(current === "detached" ? loadDockSide() : "detached");', app)
        self.assertIn("saveDockSide(target);", app)
        host = (ROOT / "browser_host" / "CompositionDemoForm.cs").read_text(encoding="utf-8")
        self.assertIn('window.DockRequested += () => SetPanelSide(_prefs.PanelLeft ? "left" : "right");', host)
        self.assertIn('["panel_dock"] = PanelLeft ? "left" : "right"', (ROOT / "browser_host" / "Ui" / "UiHostPrefs.cs").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_dock_side_without_storage(self):
        out = run_node("const A = await import('./ui/components/appearance-settings.js');"
                       "A.saveDockSide('left'); console.log(JSON.stringify({ side: A.loadDockSide() }));")
        self.assertEqual(out["side"], "right")   # no localStorage in node: falls back to the default side


class WideStylesTests(unittest.TestCase):
    """宽屏工作台: a section list beside the picked section, 任务 list + detail, and from 1200 px a
    运行 column; the same options and controls as the one-column page."""

    WIDE_ONLY = (".deck", ".task-md", ".task-pick", ".task-detail", ".log-aside", ".run-aside", ".res-overview")

    def test_wide_css_is_linked_last_and_only_applies_when_wide(self):
        html = read("index.html")
        self.assertLess(html.index('href="styles/pages.css"'), html.index('href="styles/wide.css"'))
        css = re.sub(r"/\*.*?\*/", "", read("styles/wide.css"), flags=re.S)
        selectors = [s.strip() for block in re.findall(r"([^{}]+)\{", css) for s in block.split(",")]
        self.assertTrue(selectors)
        self.assertEqual(re.findall(r"@media[^{]*", css), [])   # tiers come from layout-mode.js, not media queries
        for selector in selectors:
            # either gated on the tier, or a part only the wide render builds (and .run-aside, hidden until 1200 px)
            self.assertTrue(selector.startswith((".app[data-wide] ", ".app[data-aside] ")) or selector.startswith(self.WIDE_ONLY), selector)
        self.assertIn(".run-aside { display: none; }", css)
        self.assertIn(".app[data-aside] .side-panel { grid-template-columns: 148px minmax(0, 1fr) 320px; }", css)
        self.assertIn(".deck-view > .fold:not(.is-current) { display: none; }", css)

    def test_one_page_width(self):
        css = read("styles/wide.css")
        self.assertIn(".app[data-wide] .panel { --page-max: 1040px;", css)
        self.assertIn(".app[data-wide] .panel.home-panel { --page-max: 760px; }", css)
        self.assertEqual(len(re.findall(r"--page-max: \d+px", css)), 2)
        self.assertIn(".app[data-aside] .home-hero-actions { display: none; }", css)   # the 运行 column has 开始

    def test_wiring(self):
        app = read("app.js")
        self.assertIn("layoutMode: loadLayout(),", app)
        self.assertIn('watchLayout(document.getElementById("app"), () => store.get().layoutMode, (tier) => { if (store.get().wide !== tier) store.set({ wide: tier }); })', app)
        self.assertIn('const aside = h("div", { class: "run-aside", "aria-label": "运行与日志" });', app)
        # 2026-10-26: 「运行中」 keeps its own 停止, so the aside shows only the log there
        self.assertIn('if (state.activeTab !== "live") container.append((state.activeTab === "produce" ? wingRunView : dailyRunView)(state, actions));', app)
        self.assertIn("container.append(logAside(state, actions));", app)
        self.assertIn("renderAside(parts.aside, state, actions);", app)
        self.assertIn("selectTask: (name) => store.set({ taskSelected: name }),", app)
        self.assertIn('if (el.parentElement?.classList.contains("deck-view")) return;', app)   # a re-render keeps the pick open
        self.assertIn('row("布局", segmented("layout", LAYOUTS', read("components/appearance-settings.js"))

    def test_pages_use_the_section_list_only_when_wide(self):
        self.assertIn('state.wide ? sectionDeck(folds, { key: "settings", label: "设置分组", defaultKey: "settings-live" }) : folds', read("components/settings-panel.js"))
        self.assertIn('sectionDeck(kids, { key: "produce", label: "Produce 设置" })', read("components/wing-panel.js"))
        resources = read("components/resource-panel.js")
        self.assertIn('sectionDeck([overview, ...parts.filter((p) => p.matches("details.fold"))], { key: "resources", label: "资源分组" })', resources)
        self.assertNotIn("res-col", resources)
        tasks = read("components/task-panel.js")
        self.assertIn('h("div", { class: "task-md" },', tasks)
        self.assertIn('...(state.wide === "aside" ? [] : [logStrip(state, actions), dailyRunView(state, actions)])', tasks)
        self.assertIn('if (state.wide !== "aside") this.append(wingRunView(state, actions));', read("components/wing-panel.js"))
        common = read("components/common.js")
        self.assertIn("export function sectionDeck(folds, { key, label = \"分组\", defaultKey = null })", common)
        self.assertIn('if (el.closest?.(".deck")) return;', common)   # a deck pick is not the one-column open state

    def test_no_device_names(self):
        # 手机 / 电脑 was only an analogy, the layouts are 单栏 / 双栏
        for rel in ("components/layout-mode.js", "components/appearance-settings.js", "styles/wide.css"):
            text = read(rel)
            for word in ("手机", "电脑"):
                self.assertNotIn(word, text, rel)


if __name__ == "__main__":
    unittest.main()
