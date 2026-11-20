"""设置 → 关于数据 / 静音游戏: UI side of host commands game.data.clear / game.audio.set. Node + fake DOM; no
browser, no game input. Audio uses status.update.game_data; clearing needs a separate clear_supported capability."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"

PRELUDE = ("import './tests/fixtures/ui/fake_dom.mjs';"
           "globalThis.HTMLElement ||= class {};"
           "const m = await import('./ui/components/game-data.js');"
           "const find = (node, key) => node?.dataset?.focusKey === key ? node : (node?.childNodes || []).reduce((hit, x) => hit || find(x, key), null);")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class GameDataTests(unittest.TestCase):
    def run_node(self, body):
        result = subprocess.run(["node", "--input-type=module", "-e", PRELUDE + body], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_old_host_disables_everything_and_says_why(self):
        out = self.run_node(
            "const calls = [];"
            "const actions = { gameMute: () => calls.push('mute'), gameClearCache: () => calls.push('cache'), gameClearLogin: () => calls.push('login') };"
            "const state = { host: {}, run: { active: false } };"
            "const view = m.dataView(state, actions);"
            "const cache = find(view, 'game-clear-cache'); const login = find(view, 'game-clear-login');"
            "console.log(JSON.stringify({ model: m.gameDataModel(state.host), mute: m.muteState(state), text: view.textContent,"
            " cacheDisabled: cache.attrs?.disabled ?? cache.getAttribute?.('disabled'), loginDisabled: login.attrs?.disabled ?? login.getAttribute?.('disabled') }));")
        self.assertFalse(out["model"]["supported"])
        self.assertTrue(out["mute"]["disabled"])
        self.assertEqual(out["mute"]["hint"], "需要更新程序")
        for word in ("需要更新程序", "清理缓存", "清除登录信息"):
            self.assertIn(word, out["text"])
        self.assertIsNotNone(out["cacheDisabled"])
        self.assertIsNotNone(out["loginDisabled"])

    def test_supported_host_sizes_and_blockers(self):
        out = self.run_node(
            "const host = { game_data: { muted: true, clear_supported: true, cache_bytes: 412 * 1024 * 1024, profile_bytes: 3 * 1024 ** 3, busy: false } };"
            "const state = { host, run: { active: false } };"
            "const view = m.dataView(state, {});"
            "console.log(JSON.stringify({ model: m.gameDataModel(host), mute: m.muteState(state), text: view.textContent,"
            " free: m.gameDataBlocker(state), running: m.gameDataBlocker({ ...state, run: { active: true } }),"
            " busy: m.gameDataBlocker({ host: { game_data: { clear_supported: true, busy: true } }, run: {} }),"
            " scanning: m.gameDataBlocker({ host: { game_data: { clear_supported: true }, card_scan: { state: 'running' } }, run: {} }),"
            " bytes: [m.formatBytes(null), m.formatBytes(2048), m.formatBytes(412 * 1024 * 1024), m.formatBytes(3 * 1024 ** 3)] }));")
        self.assertTrue(out["model"]["supported"])
        self.assertTrue(out["mute"]["on"])
        self.assertFalse(out["mute"]["disabled"])
        self.assertIn("3.0 GB", out["text"])
        self.assertIn("412 MB", out["text"])
        self.assertEqual(out["free"], "")
        self.assertIn("运行", out["running"])
        self.assertTrue(out["busy"])
        self.assertIn("扫描", out["scanning"])
        self.assertEqual(out["bytes"], ["—", "2 KB", "412 MB", "3.0 GB"])

    def test_audio_only_host_enables_mute_without_enabling_clear(self):
        out = self.run_node(
            "const state = { host: { game_data: { muted: true, clear_supported: false } }, run: { active: true } };"
            "const view = m.dataView(state, {}); const cache = find(view, 'game-clear-cache');"
            "console.log(JSON.stringify({ mute: m.muteState(state), blocker: m.gameDataBlocker(state), text: view.textContent,"
            " disabled: cache.attrs?.disabled ?? cache.getAttribute?.('disabled') }));")
        self.assertEqual(out["mute"], {"on": True, "disabled": False, "hint": None})
        self.assertEqual(out["blocker"], "缓存和登录清理暂未提供")
        self.assertIsNotNone(out["disabled"])
        self.assertNotIn("需要更新", out["text"])

    def test_login_clear_needs_the_confirmation_dialog(self):
        text = (UI / "components" / "game-data.js").read_text(encoding="utf-8")
        self.assertIn("openLoginClearConfirm({ onConfirm: () => actions.gameClearLogin?.() })", text)   # never a direct call
        self.assertNotIn("document.cookie", text)
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn('bridge.request("game.data.clear", { kind: "login", confirmed: true }', app)
        self.assertIn('bridge.request("game.data.clear", { kind: "cache" }', app)
        self.assertIn('bridge.request("game.audio.set", { muted: Boolean(muted) })', app)

    def test_clear_progress_and_actual_result_are_visible(self):
        out = self.run_node(
            "const base = { supported: true, clearSupported: true };"
            "const good = { ...base, last: { op: 'cache', ok: true } };"
            "const failed = { ...base, last: { op: 'login', ok: false, reason: '清理失败' } };"
            "console.log(JSON.stringify([m.gameDataResult({ ...good, busy: true }), m.gameDataResult(good),"
            " m.gameDataResult(failed), m.dataView({ host: { game_data: { clear_supported: true, last: failed.last } } }, {}).textContent]));")
        self.assertIn("正在清理", out[0])
        self.assertIn("已清理", out[1])
        self.assertIn("重新载入", out[1])
        self.assertEqual(out[2], "清理失败")
        self.assertIn("清理失败", out[3])
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn('toast("正在清理游戏缓存…")', app)
        self.assertNotIn('toast("游戏缓存已清理")', app)

    def test_protocol_and_settings_wiring(self):
        protocol = json.loads((UI / "protocol.json").read_text(encoding="utf-8"))
        clear = protocol["commands"]["game.data.clear"]
        self.assertEqual(clear["params"]["properties"]["kind"]["enum"], ["cache", "login"])
        self.assertEqual(clear["params"]["required"], ["kind"])
        self.assertEqual(protocol["commands"]["game.audio.set"]["params"]["required"], ["muted"])
        self.assertIn("ClearBrowsingDataAsync", clear["description"])
        self.assertIn("IsMuted", protocol["commands"]["game.audio.set"]["description"])
        self.assertIn("game_data", protocol["events"]["status.update"]["data"]["properties"])
        settings = (UI / "components" / "settings-panel.js").read_text(encoding="utf-8")
        for snippet in ('title: "数据"', '"静音"', "dataView(state, actions)", "actions.gameMute"):
            self.assertIn(snippet, settings)
        home = (UI / "components" / "home-panel.js").read_text(encoding="utf-8")
        self.assertIn("autoApplyHoldings", home)   # the home tab loads the scan files first: it must auto-apply too


if __name__ == "__main__":
    unittest.main()
