"""「扫描ノウハウ」 from the UI: page gate / confirmation text / result note (node) and the
config the host generates (UiKnowhowScan.cs constants) accepted by the real CLI validator. Offline only (no game input).
The host behaviour (router, supervisor with a PowerShell child) is in MaaShinyColors.BrowserHost.exe --self-test-ui phase K."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import run_tasks

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "browser_host" / "Ui" / "UiKnowhowScan.cs"


def node(body, data=None):
    script = ("import './tests/fixtures/ui/fake_dom.mjs';"
              "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
              "const m = await import('./ui/components/knowhow-scan.js');"
              "const data = JSON.parse(process.argv[1]);" + body)
    result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(data, ensure_ascii=False)],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class HostConfigContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = HOST.read_text(encoding="utf-8")

    def option(self, name):
        match = re.search(rf'{name}\s*=\s*"""(.+?)""";', self.source)
        self.assertIsNotNone(match, name)
        return json.loads(match.group(1))

    def const(self, name):
        match = re.search(rf"\b{name}\s*=\s*(\d+|[A-Za-z_]\w*)\s*;", self.source)
        self.assertIsNotNone(match, name)
        value = match.group(1)
        return int(value) if value.isdigit() else self.const(value)  # e.g. ClickCap = ScanClickCap

    def test_fixed_options_are_valid_and_the_host_sends_no_click_cap(self):
        # No click caps; the scan's own max_inputs (checked["cap"]) still bounds one scan segment.
        for option_name, mode in (("CalibrateOption", "calibrate"), ("ScanOption", "scan")):
            with self.subTest(mode=mode):
                checked = run_tasks.knowhow_scan_options(self.option(option_name))
                self.assertEqual(checked["mode"], mode)
                self.assertLessEqual(checked["cap"], run_tasks.KNOWHOW_TASK_CAP)
        self.assertNotIn("ClickCap", self.source)
        self.assertNotIn("max_clicks", self.source)
        self.assertIsNone(self.option("ScanOption")["resume_record"])

    def test_generated_config_passes_the_supervised_validator_once_switched(self):
        from tests.test_knowhow_calibrate import switched

        with tempfile.TemporaryDirectory(dir=run_tasks.PRIVATE, prefix="knowhow-ui-") as tmp, switched(Path(tmp)):
            for option_name in ("CalibrateOption", "ScanOption"):
                config = {"preset": None, "tasks": [{"name": "KnowhowScan", "option": self.option(option_name)}],
                          "session_max_minutes": self.const("SessionMinutes"), "task_timeout_s": self.const("TaskTimeoutS"),
                          "allow_spend": []}
                scoped = run_tasks.supervised_task_config(config, live=True, offline=False, simulate_input=False)
                self.assertEqual(set(scoped["families"]), run_tasks.SUPERVISED_TASK_FAMILIES["KnowhowScan"])

    def test_command_line_is_the_card_scan_one(self):
        self.assertIn("UiCardScanSupervisor.RunArguments(repoRoot, launch)", self.source)
        self.assertNotIn("--allow", self.source)

    def test_automatic_scan_id_reaches_the_runtime_attach(self):
        option = {**self.option("ScanOption"), "scan_id": "automatic-scan", "resume_record": "latest"}
        checked = run_tasks.knowhow_scan_options(option)
        self.assertEqual(checked["attach"]["scan_id"], "automatic-scan")
        self.assertEqual(checked["attach"]["resume_record"], "latest")

    def test_newest_config_matches_the_launcher_cap(self):
        # 补读新书: BuildConfig("newest", count) = {"mode": "scan", "details": true, "newest": "auto"}, the
        # agent's count-based top-up (topup "only"); its own input budget is the one for NewestMax books
        self.assertIn('["mode"] = "scan", ["details"] = true, ["newest"] = "auto"', self.source)
        self.assertEqual(self.const("NewestMax"), 10)
        checked = run_tasks.knowhow_scan_options({"mode": "scan", "details": True, "newest": "auto"})
        self.assertEqual((checked["attach"]["topup"], checked["attach"]["newest"]), ("only", 0))
        self.assertEqual(checked["cap"], 8 + 12 * 10 + 5)
        # 读取书库 sends the plain scan option: the agent turns it into topup "prefer" (new books only for a 1..10 gap)
        self.assertEqual(run_tasks.knowhow_scan_options(self.option("ScanOption"))["attach"]["topup"], "prefer")


@unittest.skipUnless(shutil.which("node"), "node missing")
class PageTests(unittest.TestCase):
    def test_gate(self):
        out = node("""
const ready = { knowhow_scan_ready: true, knowhow_scan_reason: "ready" };
const host = { pipeline_input_enabled: true, knowhow_scan: { state: "idle", blocker: null } };
console.log(JSON.stringify([
  m.knowhowScanGate(ready, host),
  m.knowhowScanGate({ knowhow_scan_ready: false, knowhow_scan_reason: "knowhow_scan_actions_missing" }, host),
  m.knowhowScanGate(ready, { ...host, card_scan: { state: "running" } }),
  m.knowhowScanGate(ready, { ...host, knowhow_scan: { state: "running" } }),
  m.knowhowScanGate(ready, { ...host, knowhow_scan: { state: "idle", blocker: "<x>" } }),
]));""")
        ok, missing, card, running, hostile = out
        self.assertTrue(ok["ready"])
        self.assertEqual(ok["label"], "读取书库")
        self.assertFalse(missing["ready"])
        self.assertEqual(missing["reason"], "knowhow_scan_actions_missing")
        self.assertEqual(missing["title"], "此功能暂不可用：需要更新程序")
        self.assertIn("动作表", missing["detail"])  # developer view only
        self.assertEqual(card["blocker"], "card_scan_active")
        self.assertTrue(running["running"] and not running["ready"])
        self.assertEqual(hostile["blocker"], "")

    def test_confirmation_never_mentions_sell_or_protect_as_actions(self):
        out = node("console.log(JSON.stringify(m.CONFIRM_LINES));")
        self.assertEqual(set(out), {"scan", "newest"})        # 2026-11-27: 校准 left the UI
        for lines in out.values():
            self.assertTrue(any("不修改书库" in line for line in lines))

    def test_result_notes(self):
        out = node("""
const note = (s) => { const el = m.knowhowScanResultNote(s); return el ? el.textContent : null; };
console.log(JSON.stringify([
  note({ state: "finished", outcome: "partial", reason: "book_budget_exhausted", books_read: 15, needs_review: 2, sort_restore_needed: true }),
  note({ state: "running" }),
  note({ state: "finished", outcome: "failed", reason: "<img>" }),
  note({ state: "finished", outcome: "partial", reason: "detail_rows_missing", books_read: 52, resumable: true }),
]));""")
        partial, running, hostile, resumable = out
        self.assertIn("2 本需要你确认", partial)
        self.assertIn("再次扫描将重新读取全库", partial)
        self.assertIn("再次扫描从断点接续", resumable)       # 2026-11-24: resume after any stop while the game is untouched
        self.assertIn("排序仍为评价值顺", partial)
        self.assertIsNone(running)
        self.assertNotIn("<img>", hostile)
        newest = node("""const el = m.knowhowScanResultNote({ state: "finished", outcome: "newest_read", books_read: 2 });
console.log(JSON.stringify([el.textContent, m.progressText({ mode: "newest", entered: true, books: 1 })]));""")
        self.assertEqual(newest[0], "新书已读入书库（2 本）。")
        notes = node("""console.log(JSON.stringify([
  m.knowhowScanResultNote({ state: "finished", outcome: "library_up_to_date" }).textContent,
  m.knowhowScanResultNote({ state: "finished", outcome: "needs_full_scan", reason: "owned_below_inventory" }).textContent]));""")
        self.assertEqual(notes, ["书库已是最新，没有新书。", "所持数与书库对不上（所持数比书库记录少）：请读取书库。"])
        self.assertTrue(newest[1].startswith("补读新书"))

    def test_library_sync(self):
        out = node("""
const b = (count = 1) => ({ count });
console.log(JSON.stringify({
  none: m.librarySync([], null, 0).state,
  unknown: m.librarySync([b()], { owned_total: null }, 0).state,
  ok: m.librarySync([b(497), b()], { owned_total: 498 }, 0),
  pending: m.librarySync([b(498)], { owned_total: 498 }, 2),
  gap: m.librarySync([b(498)], { owned_total: 501 }, 1),
  many: m.librarySync([b(480)], { owned_total: 498 }, 0),
  sold: m.librarySync([b(498)], { owned_total: 490 }, 0).state,
  due: [m.sellPromptDue({}, m.SELL_SWITCH, "Yes"), m.sellPromptDue({}, m.SELL_SWITCH, "No"),
        m.sellPromptDue({ [m.SELL_SWITCH]: "Yes" }, m.SELL_STRATEGY, "库存够3本就卖"), m.sellPromptDue({}, m.SELL_STRATEGY, "库存够3本就卖"),
        m.sellPromptDue({}, "Produce报酬读取", "Yes")],
}));""")
        self.assertEqual((out["none"], out["unknown"], out["sold"]), ("scan", "scan", "scan"))
        self.assertEqual(out["ok"]["state"], "ok")
        self.assertEqual((out["pending"]["state"], out["pending"]["count"]), ("topup", 2))
        self.assertEqual((out["gap"]["state"], out["gap"]["count"]), ("topup", 3))   # the larger of 所持数 gap and 待读
        self.assertEqual((out["many"]["state"], out["many"]["count"]), ("scan", 18))  # more than one screen of new books
        self.assertEqual(out["due"], [True, False, True, False, False])

    def test_library_sync_state_from_the_scan_module(self):
        # scan.sync = knowhow_scan.library_sync(inv): the same state the sell executor requires (up_to_date)
        out = node("""
const d = await import("./ui/components/knowhow-data.js");
const run = (sync) => m.librarySync([{ count: 498 }], d.cleanScan({ owned_total: 498, sync }), 0);
console.log(JSON.stringify({
  up: run({ state: "up_to_date", owned_now: 500, known: 500, new: 0, pending: 0 }),
  fresh: run({ state: "new_books", owned_now: 503, known: 500, new: 3, pending: 1 }),
  others: ["too_many", "fewer_owned", "needs_full_scan", "no_baseline", "unknown"].map((state) => run({ state, owned_now: 490, known: 500, new: -10 }).state),
  bogus: run({ state: "maybe", owned_now: 1 }),
}));""")
        self.assertEqual((out["up"]["state"], out["up"]["have"], out["up"]["total"]), ("ok", 500, 500))
        self.assertEqual((out["fresh"]["state"], out["fresh"]["count"], out["fresh"]["pending"], out["fresh"]["sync"]),
                         ("topup", 3, 1, "new_books"))
        self.assertEqual(out["others"], ["scan"] * 5)
        self.assertEqual((out["bogus"]["state"], out["bogus"].get("sync")), ("ok", None))  # unknown state: count fallback

    def test_sync_prompt_and_newest_confirmation(self):
        out = node("""
const proto = Object.getPrototypeOf(document.createElement("x"));
proto.showModal = function () {}; proto.close = function () {}; proto.remove = function () {};
const buttons = (el, acc = []) => { for (const c of el.childNodes || []) { if (c.tagName === "BUTTON") acc.push(c); buttons(c, acc); } return acc; };
const press = (el, text) => buttons(el).find((x) => x.textContent === text).listeners.click[0]();
const calls = [];
const topup = m.openLibrarySyncPrompt({ state: "topup", have: 498, total: 500, pending: 2, count: 2 }, { onRead: (...a) => calls.push(a) });
const topupText = topup.textContent;
press(topup, "补读新书 2 本");
const old = m.openLibrarySyncPrompt({ state: "topup", have: 498, total: 500, pending: 2, count: 2 }, { newest: false, onRead: (...a) => calls.push(a) });
press(old, "读取书库");
const none = m.openLibrarySyncPrompt({ state: "scan", have: 0, total: null, pending: 0, count: 0 }, { onRead: (...a) => calls.push(a) });
const later = m.openLibrarySyncPrompt({ state: "scan", have: 480, total: 498, pending: 0, count: 18 }, { onRead: (...a) => calls.push(a) });
press(later, "稍后");
const confirm = m.openKnowhowScanConfirm({ mode: "newest", count: 2, onConfirm: (mode) => calls.push(["confirm", mode]) });
const confirmText = confirm.textContent;
press(confirm, "开始");
console.log(JSON.stringify({ topupText, noneText: none.textContent, ok: m.openLibrarySyncPrompt({ state: "ok" }), calls, confirmText }));""")
        self.assertIn("卖书前先读取书库", out["topupText"])
        for part in ("书库498 本", "所持500 本", "待读2 本"):
            self.assertIn(part, out["topupText"])
        self.assertIn("未读取", out["noneText"])
        self.assertIsNone(out["ok"])
        self.assertEqual(out["calls"], [["newest", 2], ["scan", 0], ["confirm", "newest"]])
        self.assertIn("补读新书 2 本", out["confirmText"])

    def test_panel_uses_the_gate_and_app_sends_mode(self):
        panel = (ROOT / "ui" / "components" / "knowhow-panel.js").read_text(encoding="utf-8")
        app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("knowhowScanGate(state?.policy, state?.host)", panel)
        self.assertIn('mode === "newest" ? { confirmed: true, mode, count } : { confirmed: true, mode: "scan" }', app)
        self.assertIn('bridge.request("knowhow.scan.start", params)', app)
        # 2026-10-25: turning 自动卖书 on (or changing its strategy) checks the library through the panel's own confirmation
        task_panel = (ROOT / "ui" / "components" / "task-panel.js").read_text(encoding="utf-8")
        self.assertIn("if (due) actions.knowhowSellCheck?.();", task_panel)
        self.assertIn("openKnowhowScanConfirm({ mode, count,", app)


if __name__ == "__main__":
    unittest.main()
