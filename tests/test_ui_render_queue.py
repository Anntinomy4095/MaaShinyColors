"""2026-10-31 (user: 展开选项后无法向下滑动看后续的选项): host-driven re-renders (status, log, readings) wait while the user
scrolls a panel; the user's own changes render at once. ui/components/render-queue.js + its wiring in ui/app.js."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A fake clock, timer list and microtask list drive the queue deterministically.
HARNESS = """
const Q = await import('./ui/components/render-queue.js');
let t = 0; const timers = []; const micro = []; const out = [];
const activity = Q.scrollActivity({ now: () => t });
const enqueue = Q.renderQueue({ render: () => out.push(['full', t]), renderTick: () => out.push(['tick', t]), activity,
  now: () => t, schedule: (fn, ms) => { timers.push({ at: t + ms, fn }); return timers.length; }, cancel: () => {},
  micro: (fn) => micro.push(fn) });
const drain = () => { while (micro.length) micro.shift()(); };
const advance = (ms) => { const end = t + ms; for (;;) { timers.sort((a, b) => a.at - b.at);
  const next = timers[0]; if (!next || next.at > end) break; timers.shift(); t = next.at; next.fn(); drain(); } t = end; };
"""


def run_node(body):
    result = subprocess.run(["node", "--input-type=module", "-e", HARNESS + body], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class RenderQueueTests(unittest.TestCase):
    def test_changes_coalesce_into_one_render(self):
        out = run_node("enqueue('status-tick'); enqueue('log'); enqueue('status-tick'); drain();"
                       "enqueue('status-tick'); drain(); console.log(JSON.stringify(out));")
        self.assertEqual(out, [["full", 0], ["tick", 0]])

    def test_background_change_waits_until_the_wheel_stops(self):
        out = run_node("activity.wheel(); enqueue('log'); drain(); const during = out.length;"
                       "advance(300); activity.wheel(); enqueue('status'); drain(); advance(300); const still = out.length;"
                       "advance(200); console.log(JSON.stringify({ during, still, out }));")
        self.assertEqual(out["during"], 0)
        self.assertEqual(out["still"], 0)
        self.assertEqual(out["out"], [["full", 700]])   # 400 ms after the last wheel notch, once

    def test_user_change_renders_at_once_while_scrolling(self):
        out = run_node("activity.wheel(); enqueue('log'); drain(); enqueue('update'); drain();"
                       "advance(1000); console.log(JSON.stringify(out));")
        self.assertEqual(out, [["full", 0]])

    def test_scrollbar_press_holds_until_release(self):
        out = run_node("activity.press(false); enqueue('status'); drain(); const content = out.length;"
                       "activity.press(true); enqueue('status'); drain(); advance(1200); const held = out.length;"
                       "activity.release(); advance(399); const quiet = out.length; advance(1);"
                       "console.log(JSON.stringify({ content, held, quiet, out }));")
        self.assertEqual(out["content"], 1)   # a press on content (a button) does not hold
        self.assertEqual(out["held"], 1)
        self.assertEqual(out["quiet"], 1)
        self.assertEqual(out["out"][-1], ["full", 1600])

    def test_hold_is_capped(self):
        out = run_node("enqueue('status'); for (let i = 0; i < 20; i++) { activity.wheel(); drain(); advance(250); }"
                       "console.log(JSON.stringify(out));")
        self.assertEqual(out[0], ["full", 3000])   # continuous scrolling still sees the host state every 3 s

    def test_lost_pointerup_stops_holding(self):
        out = run_node("activity.press(true); enqueue('status'); drain(); advance(3500); const capped = out.length;"
                       "enqueue('log'); drain(); advance(1600); console.log(JSON.stringify({ capped, out }));")
        self.assertEqual(out["capped"], 1)
        self.assertEqual(out["out"][-1], ["full", 5000])

    def test_wiring(self):
        app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('from "./components/render-queue.js"', app)
        self.assertIn("watchScrolling(document, activity)", app)
        self.assertIn('store.addEventListener("change", (event) => enqueue(event.detail?.reason))', app)
        self.assertNotIn("queueMicrotask(() => { const { full: all }", app)


if __name__ == "__main__":
    unittest.main()
