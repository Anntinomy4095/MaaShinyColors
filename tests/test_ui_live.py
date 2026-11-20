"""「运行中」 live view (ui/components/live-panel.js)."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
NODE = shutil.which("node")


def run_node(body, data):
    script = ("import './tests/fixtures/ui/fake_dom.mjs';"
              "globalThis.HTMLElement ||= class {}; globalThis.customElements ||= { define() {}, get() { return null; } };"
              "const m = await import('./ui/components/live-panel.js');"
              "const data = JSON.parse(process.argv[1]);" + body)
    result = subprocess.run([NODE, "--input-type=module", "-e", script, json.dumps(data, ensure_ascii=False)],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def snapshot(**over):
    base = {"version": 1, "run_id": "r1", "state": "running", "tasks": [{"name": "Wing", "label": "W.I.N.G.", "state": "running", "text": "进行中"}],
            "current": "Wing", "step": "打开日程", "progress": {"done": 0, "total": 1}, "clicks": {"used": 3, "cap": 170},
            "elapsed_s": 75, "age_s": 1, "wing": {"season": 1, "weeks_left": 6, "stats": {"Vo": 150, "Da": "x"}, "sp": 60,
                                                   "decision": {"chosen": "Vo 课程（支援 3 人）", "options": [{"label": "A", "score": 1, "chosen": True}] * 9},
                                                   "events": [{"kind": "evil", "text": "t"}] * 20}}
    base.update(over)
    return base


@unittest.skipUnless(NODE, "node not installed")
class LiveModelTests(unittest.TestCase):
    def test_clean_live_validates_and_bounds(self):
        out = run_node("const c = m.cleanLive(data.raw); console.log(JSON.stringify({c, bad: m.cleanLive({version: 2}), none: m.cleanLive(null)}));",
                       {"raw": snapshot()})
        c = out["c"]
        self.assertIsNone(out["bad"])
        self.assertIsNone(out["none"])
        self.assertEqual(c["wing"]["stats"], {"Vo": 150})            # non-integer reading dropped, never guessed
        self.assertEqual(len(c["wing"]["decision"]["options"]), 4)
        self.assertEqual(len(c["wing"]["events"]), 12)
        self.assertEqual(c["wing"]["events"][0]["kind"], "info")     # unknown kinds are neutral
        fail = run_node("console.log(JSON.stringify(m.cleanLive(data.raw)));",
                        {"raw": snapshot(wing={"events": [{"kind": "fail", "text": "失败 Vo−8"}]})})
        self.assertEqual(fail["wing"]["events"][0]["kind"], "fail")      # a failed lesson
        self.assertEqual(c["clicks"], {"used": 3, "cap": 170})

    def test_running_stale_and_finished_windows(self):
        body = ("const r = (raw, run) => { const mo = m.liveModel({ host: { run_live: raw }, run: run || {}, taskStatus: {} });"
                " return mo ? [mo.state, mo.stale, m.liveRunning(mo)] : null; };"
                "console.log(JSON.stringify(data.cases.map(([raw, run]) => r(raw, run))));")
        cases = [[snapshot(), None], [snapshot(age_s=500), None], [snapshot(age_s=500), {"active": True, "id": "r1"}],
                 [snapshot(state="finished", age_s=60), None], [snapshot(state="finished", age_s=4000), None]]
        out = run_node(body, {"cases": cases})
        self.assertEqual(out[0], ["running", False, True])
        self.assertIsNone(out[1])                                     # a crashed run's old file: no takeover
        self.assertEqual(out[2], ["running", True, False])            # our own run: shown, marked stale, no takeover
        self.assertIsNone(out[3])                                     # a finished run: the tab hides again
        self.assertIsNone(out[4])

    def test_derived_queue_without_run_live(self):
        body = ("const st = { host: {}, run: { active: true, id: 'u1', tasks: ['Shop', 'Training'] }, taskStatus: { Shop: { status: 'succeeded' }, Training: { status: 'running' } },"
                " schema: { tasks: [] }, annotations: { tasks: { Shop: { name: '商店' }, Training: { name: '训练' } } } };"
                "const mo = m.liveModel(st); console.log(JSON.stringify(mo));")
        mo = run_node(body, {})
        self.assertEqual([t["state"] for t in mo["tasks"]], ["done", "running"])
        self.assertEqual(mo["step"], "训练")
        self.assertEqual(mo["progress"], {"done": 1, "total": 2})

    def test_finished_segment_keeps_active_gui_run_visible(self):
        body = ("const st = { host: { run_live: data.raw }, run: { active: true, id: 'gui1', tasks: ['Wing'] },"
                " taskStatus: { Wing: { status: 'running' } }, schema: { tasks: [] }, annotations: { tasks: {} } };"
                "const running = m.liveModel(st); st.run.stopping = true; const stopping = m.liveModel(st);"
                "st.run.active = false; console.log(JSON.stringify({running, stopping, ended: m.liveModel(st)}));")
        out = run_node(body, {"raw": snapshot(state="finished")})
        self.assertTrue(out["running"]["derived"])
        self.assertEqual(out["running"]["state"], "running")
        self.assertEqual(out["running"]["current"], "Wing")
        self.assertEqual(out["stopping"]["state"], "stopping")
        self.assertIsNone(out["ended"])

    def test_live_key_ignores_clock_fields(self):
        body = ("const k = (raw) => m.liveKey({ host: { run_live: raw }, run: {}, taskStatus: {} });"
                "console.log(JSON.stringify([k(data.a) === k(data.b), k(data.a) === k(data.c)]));")
        out = run_node(body, {"a": snapshot(), "b": snapshot(elapsed_s=99, age_s=3, updated_at="x"), "c": snapshot(step="选择休息")})
        self.assertEqual(out, [True, False])


class LiveWiringTests(unittest.TestCase):
    def test_app_header_and_bridge_wiring(self):
        app = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn('live: "sc-live-panel"', app)
        self.assertIn("liveBanner(state, actions)", app)
        self.assertIn('activeTab: "live", liveTakeover: "pending"', app)     # 开始 takes over at once
        self.assertIn("seen !== model.runId", app)                            # once per run, never pulls back
        header = (UI / "components" / "header.js").read_text(encoding="utf-8")
        self.assertIn('{ key: "live", label: "运行中", live: true }', header)
        self.assertIn("run_live: true", header)                               # volatile: refreshed in place
        bridge = (UI / "components" / "bridge.js").read_text(encoding="utf-8")
        self.assertIn("run_live", bridge)
        self.assertIn('params.get("live") !== "wing"', bridge)

    def test_view_has_no_developer_jargon_and_only_the_existing_stop(self):
        js = (UI / "components" / "live-panel.js").read_text(encoding="utf-8")
        self.assertIn('actions.stopRun("cooperative")', js)
        self.assertNotRegex(js, r"bridge\.request|run\.start|spend")
        strings = " ".join(re.findall(r'"([^"\n]*[一-鿿][^"\n]*)"', js))
        for word in ("节点", "node", "intent", "SC.", "run_id"):
            self.assertNotIn(word, strings)
        css = (UI / "styles" / "live.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")                      # tokens only

    def test_synthetic_fixture_has_no_private_data(self):
        text = (UI / "fixtures" / "live-demo.js").read_text(encoding="utf-8")
        for word in ("dev_assets", ".png", "token", "cookie", "Users", "SC."):
            self.assertNotIn(word, text)
        self.assertIn("made-up", text)


if __name__ == "__main__":
    unittest.main()
