"""设置 → 更新: 下载更新 / 重启并更新 (2026-11-27). Text and buttons from status.update.support (node, fake DOM)."""
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def node(body):
    script = ("import './tests/fixtures/ui/fake_dom.mjs';"
              "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
              "const m = await import('./ui/components/support-settings.js');" + body)
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


VIEW = """
const view = (support, running = false) => {
  const calls = [];
  const state = { supportInfo: { repo: "o/r", version: "0.1.1" }, prefs: {}, schema: {}, bridgeKind: "host",
    run: { active: running }, host: { support } };
  const el = m.updateView(state, { support: (op) => calls.push(op), setPref() {} });
  const found = [];
  const walk = (node) => { for (const c of node.childNodes || []) { if (c.tagName === "BUTTON") found.push(c); walk(c); } };
  walk(el);
  const buttons = found.map((b) => ({ text: b.textContent,
    disabled: b.disabled === true || (b.attributes && "disabled" in b.attributes && b.attributes.disabled !== "false") }));
  return { text: el.textContent, buttons };
};
"""


class UpdateViewTests(unittest.TestCase):
    def test_states(self):
        out = node(VIEW + """console.log(JSON.stringify({
  newer: view({ package: true, update: { state: "done", latest: "v0.1.2", newer: true } }),
  source: view({ package: false, update: { state: "done", latest: "v0.1.2", newer: true } }),
  downloading: view({ package: true, update: { state: "downloading", latest: "v0.1.2", bytes: 50, total: 200 } }),
  ready: view({ package: true, update: { state: "ready", latest: "v0.1.2", files: 12 } }),
  readyRunning: view({ package: true, update: { state: "ready", latest: "v0.1.2", files: 12 } }, true),
  failed: view({ package: true, update: { state: "download_failed", reason: "digest" } }),
  applied: view({ package: true, update: { state: "idle" }, last_apply: { ok: true, version: "0.1.2" } }),
  restored: view({ package: true, update: { state: "idle" }, last_apply: { ok: false, version: "0.1.2", message: "x" } }),
}));""")
        labels = lambda v: [b["text"] for b in v["buttons"]]  # noqa: E731
        self.assertIn("下载更新", labels(out["newer"]))
        self.assertNotIn("下载更新", labels(out["source"]))          # source checkout: no launcher, no download
        self.assertIn("正在下载 v0.1.2 25%", out["downloading"]["text"])
        self.assertNotIn("下载更新", labels(out["downloading"]))
        self.assertIn("重启并更新", labels(out["ready"]))
        self.assertIn("12 个文件", out["ready"]["text"])
        restart = next(b for b in out["readyRunning"]["buttons"] if b["text"] == "重启并更新")
        self.assertTrue(restart["disabled"])                          # never while a task runs
        self.assertIn("文件校验失败", out["failed"]["text"])
        self.assertIn("下载更新", labels(out["failed"]))                # retry after a failed download
        self.assertIn("已更新到 0.1.2", out["applied"]["text"])
        self.assertIn("已恢复原版本", out["restored"]["text"])


if __name__ == "__main__":
    unittest.main()
