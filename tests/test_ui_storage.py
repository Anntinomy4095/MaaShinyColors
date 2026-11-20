"""设置 → 存储: UI side of host commands storage.status / storage.clean / storage.auto. Node + fake DOM; no
browser, no game input, nothing deleted. Until the host reports status.update.storage the section says 「需要更新程序」."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"

PRELUDE = ("import './tests/fixtures/ui/fake_dom.mjs';"
           "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {} };"
           "const m = await import('./ui/components/storage.js');"
           "const MB = 1024 * 1024;"
           "const cat = (key, bytes, cleanable) => ({ key, bytes, files: 3, cleanable_bytes: cleanable, cleanable_files: cleanable ? 2 : 0 });"
           "const host = (extra = {}) => ({ storage: { state: 'idle', categories: [cat('samples', 300 * MB, 300 * MB), cat('videos', 2048 * MB, 2048 * MB),"
           " cat('logs', 5 * MB, 0), cat('cache', 4096, 4096)], other_bytes: 40 * MB, total_bytes: 2393 * MB, auto: { enabled: false, days: 14 }, blocker: null, last: null, ...extra } });"
           "const find = (node, pred, out = []) => { for (const c of node.childNodes || []) { if (pred(c)) out.push(c); find(c, pred, out); } return out; };"
           "const byKey = (node, key) => find(node, (c) => c.dataset?.focusKey === key)[0];")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class StorageUiTests(unittest.TestCase):
    def run_node(self, body):
        result = subprocess.run(["node", "--input-type=module", "-e", PRELUDE + body], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_model_summary_and_old_host(self):
        out = self.run_node(
            "console.log(JSON.stringify({ old: m.storageModel({}), oldSummary: m.storageSummary(m.storageModel({})),"
            " summary: m.storageSummary(m.storageModel(host())),"
            " auto: m.storageSummary(m.storageModel(host({ auto: { enabled: true, days: 7 } }))),"
            " scanning: m.storageSummary(m.storageModel({ storage: { state: 'scanning', categories: null, auto: {} } })),"
            " badDays: m.storageModel(host({ auto: { enabled: true, days: 3 } })).auto.days,"
            " last: m.lastText({ op: 'clean', ok: true, freed_bytes: 35 * MB, files: 120, failed: 0 }),"
            " prune: m.lastText({ op: 'prune', ok: false, freed_bytes: MB, files: 4, failed: 1 }) }));")
        self.assertFalse(out["old"]["supported"])
        self.assertEqual(out["oldSummary"], "需要更新程序")
        self.assertEqual(out["summary"], "共 2.3 GB")
        self.assertEqual(out["auto"], "共 2.3 GB · 自动 7 天")
        self.assertEqual(out["scanning"], "计算中…")
        self.assertEqual(out["badDays"], 14)
        self.assertEqual(out["last"], "上次清理：释放 35 MB（120 个文件）")
        self.assertIn("1 个未能删除", out["prune"])

    def test_view_buttons_blockers_and_actions(self):
        out = self.run_node(
            "const calls = [];"
            "const actions = { storageStatus: (r) => calls.push(['status', r]), storageClean: (k) => calls.push(['clean', k]),"
            " storageAuto: (e, d) => calls.push(['auto', e, d]) };"
            "const view = m.storageView({ host: host(), run: {} }, actions);"
            "const dis = (key, v = view) => byKey(v, key)?.getAttribute('disabled') !== null;"
            "byKey(view, 'storage-auto').listeners.click[0]();"
            "byKey(view, 'storage-days-30').listeners.click[0]();"
            "byKey(view, 'storage-refresh').listeners.click[0]();"
            "const running = m.storageView({ host: host(), run: { active: true } }, actions);"
            "const external = m.storageView({ host: host({ blocker: 'external_run' }), run: {} }, actions);"
            "const scanning = m.storageView({ host: { storage: { state: 'scanning', categories: null, auto: {} } }, run: {} }, actions);"
            "const old = m.storageView({ host: {}, run: {} }, actions);"
            "console.log(JSON.stringify({ text: view.textContent, calls,"
            " samples: dis('storage-clean-samples'), logs: dis('storage-clean-logs'),"
            " running: dis('storage-clean-samples', running), external: external.textContent,"
            " videoRow: find(view, (n) => n.dataset?.storage === 'videos').length,"
            " scanning: scanning.textContent, scanDis: dis('storage-clean-samples', scanning), old: old.textContent }));")
        for label in ("截图样本", "运行记录", "其他", "自动清理", "保留天数", "300 MB"):
            self.assertIn(label, out["text"])
        self.assertNotIn("录像", out["text"])
        self.assertIn(["status", False], out["calls"])          # opening the section asks the host (throttled in app.js)
        self.assertNotIn(["auto", True, 14], out["calls"])      # 2026-10-26: turning it on asks first (openAutoCleanConfirm)
        self.assertIn(["auto", False, 30], out["calls"])        # days while off delete nothing: no question
        self.assertIn(["status", True], out["calls"])
        self.assertFalse(out["samples"])
        self.assertTrue(out["logs"])                              # nothing cleanable (recent logs are kept)
        self.assertTrue(out["running"])
        self.assertIn("任务正在操作游戏", out["external"])
        self.assertEqual(out["videoRow"], 0)
        self.assertIn("计算中…", out["scanning"])
        self.assertTrue(out["scanDis"])
        self.assertIn("需要更新程序", out["old"])

    def test_auto_clean_asks_before_it_deletes(self):
        # 2026-10-26: 自动清理 on, or fewer days while it is on, asks first; more days or off does not
        out = self.run_node(
            "globalThis.document.createElement = ((orig) => (tag) => { const el = orig(tag); if (tag === 'dialog') { el.showModal = () => {}; el.close = () => {}; el.remove = () => {}; } return el; })(globalThis.document.createElement);"
            "const calls = [];"
            "const actions = { storageStatus() {}, storageAuto: (e, d) => calls.push(['auto', e, d]) };"
            "const off = m.storageView({ host: host(), run: {} }, actions);"
            "byKey(off, 'storage-auto').listeners.click[0]();"
            "const dialog = globalThis.document.body.childNodes.at(-1);"
            "const text = dialog.textContent; const before = calls.length;"
            "find(dialog, (n) => n.dataset?.storageConfirm === 'auto')[0].listeners.click[0]();"
            "const on = m.storageView({ host: host({ auto: { enabled: true, days: 14 } }), run: {} }, actions);"
            "byKey(on, 'storage-days-30').listeners.click[0]();"
            "const kept = calls.length;"
            "byKey(on, 'storage-days-7').listeners.click[0]();"
            "const fewer = calls.length;"
            "byKey(on, 'storage-auto').listeners.click[0]();"
            "console.log(JSON.stringify({ text, before, kept, fewer, calls }));")
        self.assertIn("自动清理：保留 14 天", out["text"])
        self.assertNotIn("手牌识别", out["text"])                 # 2026-10-29: the hand cache is never auto-cleaned
        self.assertIn("删除后不能恢复", out["text"])
        self.assertEqual(out["before"], 0)
        self.assertEqual(out["calls"][:2], [["auto", True, 14], ["auto", True, 30]])
        self.assertEqual((out["kept"], out["fewer"]), (2, 2))    # 7 days while on waits for its own confirmation
        self.assertEqual(out["calls"][-1], ["auto", False, 14])

    def test_legacy_video_category_is_not_exposed(self):
        out = self.run_node(
            "const view = m.storageView({ host: host(), run: {} }, {});"
            "const model = m.storageModel(host());"
            "console.log(JSON.stringify({ text: view.textContent, keys: model.categories.map((c) => c.key) }));")
        self.assertNotIn("录像", out["text"])
        self.assertEqual(out["keys"], ["samples", "logs", "cache"])

    def test_hand_cache_row_cleans_like_the_others(self):
        out = self.run_node(
            "const calls = []; const view = m.storageView({ host: host(), run: {} }, { storageStatus() {}, storageClean: (k) => calls.push(k) });"
            "const row = find(view, (n) => n.dataset?.storage === 'cache')[0];"
            "console.log(JSON.stringify({ text: row.textContent, disabled: byKey(view, 'storage-clean-cache').disabled ?? null }));")
        self.assertIn("手牌识别", out["text"])
        self.assertIn("4 KB", out["text"])
        self.assertIsNone(out["disabled"])

    def test_settings_panel_uses_storage_and_app_sends_protocol_commands(self):
        panel = (UI / "components" / "settings-panel.js").read_text(encoding="utf-8")
        self.assertIn('key: "settings-storage", title: "存储"', panel)
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn('bridge.request("storage.clean", { category, confirmed: true })', app)
        self.assertIn('bridge.request("storage.auto", { enabled: Boolean(enabled), days })', app)
        protocol = json.loads((UI / "protocol.json").read_text(encoding="utf-8"))
        clean = protocol["commands"]["storage.clean"]["params"]
        self.assertEqual(clean["properties"]["confirmed"], {"const": True})
        self.assertEqual(clean["properties"]["category"]["enum"], ["samples", "logs", "cache"])
        self.assertEqual(protocol["commands"]["storage.auto"]["params"]["properties"]["days"]["enum"], [7, 14, 30])
        self.assertIn("storage", protocol["events"]["status.update"]["data"]["properties"])
        mirror = (UI / "protocol.js").read_text(encoding="utf-8")
        self.assertIn('"storage.clean"', mirror)
        bridge = (UI / "components" / "bridge.js").read_text(encoding="utf-8")
        self.assertIn('case "storage.clean":', bridge)   # the preview never deletes


if __name__ == "__main__":
    unittest.main()
