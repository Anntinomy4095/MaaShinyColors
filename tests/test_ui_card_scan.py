"""「扫描持有卡」 from the UI: host-launched supervised CardScan, offline only (no game input).

Covers the page logic (gate / confirmation / progress / result text, node), the page <-> protocol <-> host source contracts, and
that the config the host generates is accepted by the real CLI validator with the same budget as the documented supervised
run. The host behaviour itself (router, supervisor with a PowerShell child, outcomes, kill, import copy) is in
`MaaShinyColors.BrowserHost.exe --self-test-ui` phase C.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tools import run_tasks

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
HOST = ROOT / "browser_host" / "Ui"


def node(body, data=None):
    script = ("import './tests/fixtures/ui/fake_dom.mjs';"
              "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
              "const m = await import('./ui/components/card-scan.js');"
              "const data = JSON.parse(process.argv[1]);" + body)
    result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(data, ensure_ascii=False)],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class HostConfigContractTests(unittest.TestCase):
    """The constants in UiCardScan.cs generate a config the CLI's supervised-task validator accepts unchanged."""

    @classmethod
    def setUpClass(cls):
        cls.source = (HOST / "UiCardScan.cs").read_text(encoding="utf-8")

    def const(self, name):
        match = re.search(rf"\b{name}\s*=\s*(\d+)\s*;", self.source)
        self.assertIsNotNone(match, name)
        return int(match.group(1))

    def config(self):
        scrolls, frames, holds = (self.const(n) for n in ("MaxScrollsPerTab", "MaxFrames", "MaxHolds"))
        self.assertNotIn("ClickCap", self.source)   # no click caps
        return {"preset": None,
                "tasks": [{"name": "CardScan", "option": {"tabs": ["support", "produce"], "max_scrolls_per_tab": scrolls,
                                                          "max_frames": frames, "max_holds": holds}}],
                "session_max_minutes": self.const("SessionMinutes"), "task_timeout_s": self.const("TaskTimeoutS"),
                "allow_spend": []}

    def test_budget_is_the_documented_full_library_run(self):
        config = self.config()
        self.assertEqual((self.const("MaxScrollsPerTab"), self.const("MaxFrames"), self.const("MaxHolds"), self.const("SessionMinutes")),
                         (80, 400, 10, 50))
        # Same shape as the private config the CLI run used (dev_assets/private/cardscan-sample-20261009/resume-config.json).
        scoped = run_tasks.supervised_task_config(config, live=True, offline=False, simulate_input=False)
        self.assertEqual(scoped["allow_spend"], [])
        options = run_tasks.card_scan_options(config["tasks"][0]["option"])
        self.assertEqual((options.tabs, options.max_scrolls_per_tab, options.max_frames, options.max_holds),
                         (("support", "produce"), 80, 400, 10))

    def test_source_builds_exactly_these_keys_and_no_spend(self):
        for needle in ('["preset"] = null', '["name"] = "CardScan"', '["allow_spend"] = new JsonArray()', '["session_max_minutes"]',
                       'new JsonArray("support", "produce")'):
            self.assertIn(needle, self.source)
        self.assertNotIn("max_clicks", self.source)   # no click caps

    def test_command_line_is_the_supervised_live_task_and_nothing_else(self):
        arguments = re.search(r"RunArguments\(.*?=> new\[\]\s*\{(.*?)\};", self.source, re.S).group(1)
        for flag in ("--live", "--supervised-task", "--config", "--session", "--summary", "--events", "--stop-file"):
            self.assertIn(f'"{flag}"', arguments)
        self.assertNotRegex(arguments, r'--allow|--simulate-input|--offline-frames|--supervised-startup|--preset|--tasks')
        self.assertIn(".venv", self.source)
        self.assertNotIn("ProcessStartInfo(\"cmd", self.source)

    def test_host_never_writes_the_profile_or_the_policy(self):
        for needle in ("project_policy", "profile.json", "UiProfileStore", "owned_cards"):
            self.assertNotIn(needle, self.source)
        # Only the two fixed module names are published into the served ui folder.
        self.assertIn('{ "owned-levels.js", "inventory-scan.js" }', self.source)

    def test_router_and_dock_wiring(self):
        router = (HOST / "UiMessageRouter.cs").read_text(encoding="utf-8")
        dock = (HOST / "UiGameDock.cs").read_text(encoding="utf-8")
        for command in ("cards.scan.start", "cards.scan.stop", "cards.scan.status"):
            self.assertIn(f'case "{command}":', router)
        self.assertIn("_cardScan?.Active == true", router)                       # run.start refuses while scanning
        self.assertIn("UiCardScanSupervisor(root, CardScanBlocker, sessionPath)", dock)
        self.assertIn("UiCardScanGate.Blocker(", dock)
        self.assertIn("CardScan.Dispose();", dock)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PageLogicTests(unittest.TestCase):
    def test_gate_states(self):
        out = node(
            "const ready = { card_scan_ready: true, card_scan_reason: 'ready' };"
            "const closed = { card_scan_ready: false, card_scan_reason: 'card_scan_entry_disabled' };"
            "const host = (extra = {}) => ({ pipeline_input_enabled: true, engine_running: false, ...extra });"
            "const g = (p, h) => { const r = m.cardScanGate(p, h); return { ready: r.ready, running: r.running, blocker: r.blocker, label: r.label, title: r.title }; };"
            "console.log(JSON.stringify({"
            "  nohost: g(ready, undefined), ok: g(ready, host()), closed: g(closed, host()),"
            "  noinput: g(ready, host({ pipeline_input_enabled: false })), engine: g(ready, host({ engine_running: true })),"
            "  hostblock: g(ready, host({ card_scan: { state: 'idle', blocker: 'other_input_activity' } })),"
            "  hostile: g(ready, host({ card_scan: { state: 'idle', blocker: '<img src=x>' } })),"
            "  running: g(ready, host({ card_scan: { state: 'running', blocker: null } })),"
            "  stopping: g(ready, host({ card_scan: { state: 'stopping' } })),"
            "  finished: g(ready, host({ card_scan: { state: 'finished', blocker: null } })) }));")
        self.assertTrue(out["nohost"]["ready"] and out["ok"]["ready"] and out["finished"]["ready"])
        self.assertEqual(out["closed"]["label"], "扫描持有卡")
        self.assertEqual(out["closed"]["title"], "此功能暂不可用：需要更新程序")
        self.assertFalse(out["closed"]["ready"])
        self.assertEqual(out["ok"]["label"], "扫描持有卡")
        self.assertIn("程序未启用游戏操作", out["noinput"]["title"])
        self.assertEqual((out["noinput"]["blocker"], out["engine"]["blocker"]),
                         ("pipeline_input_disabled", "engine_active"))
        self.assertFalse(any(out[k]["ready"] for k in ("noinput", "engine", "hostblock", "running", "stopping")))
        self.assertEqual(out["hostblock"]["blocker"], "other_input_activity")
        self.assertEqual(out["hostile"]["blocker"], "")                       # unexpected text is never trusted or displayed
        self.assertTrue(out["running"]["running"] and out["stopping"]["running"])

    def test_confirmation_text_and_progress(self):
        out = node(
            "const lines = m.CONFIRM_LINES.join('|');"
            "const clicks = []; const scan = { state: 'running', tab: 'produce', scrolls: 12, frames: 14, elapsed_s: 125 };"
            "const block = m.cardScanProgress(scan, { onStop: () => clicks.push('stop') });"
            "const find = (n, f) => f(n) ? n : (n.childNodes || []).reduce((hit, c) => hit || find(c, f), null);"
            "const btn = find(block, (n) => n.tagName === 'BUTTON');"
            "for (const fn of btn.listeners.click) fn();"
            "const stopping = m.cardScanProgress({ ...scan, state: 'stopping' }, {});"
            "const sbtn = find(stopping, (n) => n.tagName === 'BUTTON');"
            "const line = find(block, (n) => n.dataset?.cardScanLine);"
            "const holder = { querySelector: () => line };"
            "m.updateCardScanProgress(holder, { tab: 'support', scrolls: 3, frames: 4, elapsed_s: 9 });"
            "console.log(JSON.stringify({ lines, text: m.progressText(scan), clicks, stoppingDisabled: sbtn.getAttribute('disabled') !== null,"
            "  stoppingText: sbtn.textContent, updated: line.textContent, initial: m.progressText({}), block: block.textContent }));")
        for needle in ("约 10–20 分钟", "勿触碰", "停止", "需要你确认"):
            self.assertIn(needle, out["lines"])
        self.assertEqual(out["text"], "P 卡标签 · 已滚动 12 次 · 已读 14 帧 · 用时 02:05")
        self.assertEqual(out["clicks"], ["stop"])
        self.assertTrue(out["stoppingDisabled"])
        self.assertEqual(out["stoppingText"], "停止中…")
        self.assertEqual(out["updated"], "S 卡标签 · 已滚动 3 次 · 已读 4 帧 · 用时 00:09")
        self.assertEqual(out["initial"], "正在进入列表 · 已滚动 0 次 · 已读 0 帧 · 用时 —")

    def test_result_notes_are_plain_and_never_claim_unproven_success(self):
        out = node(
            "const note = (s) => { const n = m.cardScanResultNote(s); return n ? n.textContent : null; };"
            "console.log(JSON.stringify({"
            "  running: note({ state: 'running' }), idle: note({ state: 'idle' }),"
            "  ok: note({ state: 'finished', outcome: 'scanned', imported: true, cards: 233 }),"
            "  noimport: note({ state: 'finished', outcome: 'scanned', imported: false, reason: 'import_files_missing' }),"
            "  partial: note({ state: 'finished', outcome: 'partial', imported: false, reason: 'scroll_budget_exhausted',"
            "    tabs: [{ tab: 'support', complete: false, accepted: 212 }, { tab: 'produce', complete: false, accepted: 0 }] }),"
            "  stopped: note({ state: 'finished', outcome: 'stopped', reason: 'user_stop' }),"
            "  uncertain: note({ state: 'finished', outcome: 'uncertain', reason: 'killed_after_stop' }),"
            "  failed: note({ state: 'finished', outcome: 'failed', reason: 'engine_exit_3' }),"
            "  hostile: note({ state: 'finished', outcome: 'failed', reason: '<img src=x onerror=1>' }),"
            "  reasons: [m.reasonText('overlap_lost'), m.reasonText('engine_exit_7'), m.reasonText('weird_code'), m.reasonText('a b')] }));")
        self.assertIsNone(out["running"])
        self.assertIsNone(out["idle"])
        self.assertIn("233", out["ok"])
        self.assertIn("已确认", out["ok"])
        self.assertIn("未导入", out["noimport"])
        self.assertIn("滚动次数用完", out["partial"])
        self.assertIn("S 卡读到 212 格", out["partial"])
        self.assertIn("未导入", out["partial"])
        self.assertIn("你停止了扫描", out["stopped"])
        self.assertIn("结果不确定", out["uncertain"])
        self.assertIn("异常退出（3）", out["failed"])
        self.assertNotIn("<img", out["hostile"])
        self.assertEqual(out["reasons"][0], "滚动后与上一页对不上（页面可能还在加载或有弹窗）")
        self.assertEqual(out["reasons"][1], "扫描程序异常退出（7）")
        self.assertEqual(out["reasons"][3], "")

    def test_scan_version_cache_buster(self):
        out = node(
            "const v = await import('./ui/components/scan-version.js');"
            "const a = v.scanSuffix(); v.bumpScanVersion(); const b = v.scanSuffix(); v.bumpScanVersion();"
            "console.log(JSON.stringify([a, b, v.scanSuffix()]));")
        self.assertEqual(out, ["", "?v=1", "?v=2"])

    def test_every_scan_module_import_uses_the_cache_buster(self):
        for name in ("team-import.js", "team-levels-import.js", "resource-panel.js"):
            text = (UI / "components" / name).read_text(encoding="utf-8")
            self.assertIn("scanSuffix()", text, name)
        self.assertNotRegex((UI / "components" / "team-import.js").read_text(encoding="utf-8"), r'import\("\.\./game-assets/inventory-scan\.js"\)')
        self.assertNotRegex((UI / "components" / "team-levels-import.js").read_text(encoding="utf-8"), r'import\("\.\./game-assets/owned-levels\.js"\)')

    def test_volatile_card_scan_fields_do_not_rebuild_the_panel(self):
        out = node(
            "const h = await import('./ui/components/header.js');"
            "const a = { connected: true, card_scan: { state: 'running', tab: 'support', scrolls: 1, frames: 2, elapsed_s: 3 } };"
            "const tick = { ...a, card_scan: { ...a.card_scan, scrolls: 2, frames: 3, elapsed_s: 4 } };"
            "const tab = { ...a, card_scan: { ...a.card_scan, tab: 'produce' } };"
            "console.log(JSON.stringify([h.hostChange(a, tick), h.hostChange(a, tab), h.hostChange(a, a)]));")
        self.assertEqual(out, ["tick", "change", "same"])


if __name__ == "__main__":
    unittest.main()
