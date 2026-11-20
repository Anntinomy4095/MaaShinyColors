"""设置 → 窗口 (2026-10-12): 后台运行 switch and 现在隐藏 moved off the main window. Node + fake DOM; no browser, no input."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = ("import './tests/fixtures/ui/fake_dom.mjs';"
           "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {} };"
           "const m = await import('./ui/components/settings-panel.js');")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class WindowBackgroundTests(unittest.TestCase):
    def run_node(self, body):
        result = subprocess.run(["node", "--input-type=module", "-e", PRELUDE + body], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_model_states(self):
        out = self.run_node(
            "const w = (x) => m.windowModel({ window_background: x });"
            "console.log(JSON.stringify({ none: m.windowModel({}), on: w({ supported: true, enabled: true, args_active: true, hide_mode: 'none' }),"
            " off: w({ supported: true, enabled: false, args_active: true, hide_mode: 'none' }),"
            " forced: w({ supported: true, enabled: true, forced: true, args_active: true, hide_mode: 'cloaked' }) }));")
        self.assertFalse(out["none"]["supported"])
        self.assertTrue(out["on"]["enabled"])
        self.assertFalse(out["off"]["enabled"] or out["off"]["forced"])
        self.assertTrue(out["forced"]["forced"] and out["forced"]["hidden"])

    def test_old_host_disables_and_switch_asks_the_host(self):
        out = self.run_node(
            "const calls = [];"
            "const actions = { windowBackground: (v) => calls.push(['set', v]), windowHide: () => calls.push(['hide']) };"
            "const view = m.windowView({ host: { window_background: { supported: true, enabled: true, args_active: true, hide_mode: 'none' } } }, actions);"
            "const old = m.windowView({ host: {} }, actions);"
            "console.log(JSON.stringify({ text: view.textContent, old: old.textContent }));")
        self.assertIn("后台运行", out["text"])
        self.assertIn("现在隐藏", out["text"])
        self.assertEqual(out["old"], "后台运行现在隐藏")   # the fold summary says 需要更新程序; the controls are disabled

    def test_app_sends_only_the_window_command(self):
        app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('bridge.request("window.background", { op: "set", enabled: Boolean(enabled) })', app)
        self.assertIn('bridge.request("window.background", { op: "hide" })', app)


if __name__ == "__main__":
    unittest.main()
