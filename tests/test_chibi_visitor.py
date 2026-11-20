"""Run the visitor clock, lifecycle, and drag bounds without waiting in real time."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ChibiVisitorTests(unittest.TestCase):
    def test_visible_time_one_visitor_drag_close_and_restart(self):
        script = """
import assert from "node:assert/strict";
import "./tests/fixtures/ui/fake_dom.mjs";
import { createChibiVisitor } from "./ui/components/chibi-visitor.js";
const doc = document;
const visibility = new Set();
doc.addEventListener = (_, callback) => visibility.add(callback);
doc.removeEventListener = (_, callback) => visibility.delete(callback);
function show(value) {
  doc.hidden = !value;
  for (const callback of visibility) callback();
}
let resize, systemMotionChanged;
const motion = {
  matches: false,
  addEventListener: (_, callback) => { systemMotionChanged = callback; },
  removeEventListener: () => { systemMotionChanged = null; },
};
doc.defaultView = {
  innerWidth: 500, innerHeight: 400,
  matchMedia: () => motion,
  ResizeObserver: class {
    constructor(fn) { resize = fn; }
    observe() {}
    disconnect() { resize = null; }
  },
  addEventListener() {}, removeEventListener() {},
};
const proto = Object.getPrototypeOf(doc.body);
Object.defineProperty(proto, "style", { get() { return this.styles ||= {}; } });
proto.setPointerCapture = () => {};
proto.matches = () => false;
proto.hidePopover = () => {};
proto.remove = function () {
  const parent = this.parentNode;
  parent.childNodes = parent.childNodes.filter(child => child !== this);
};
let areaBox = { left: 100, top: 60, right: 420, bottom: 320 };
const area = { ownerDocument: doc, getBoundingClientRect: () => areaBox };
let clock = 0, timer, delay, release, starts = 0, destroyed = 0, reactions = 0, exclude;
let hidden = 0;
const played = [];
const actor = {
  canvas: doc.createElement("canvas"), key: "mano", width: 148, height: 168,
  characters: [{ key: "mano", name: "真乃" }, { key: "hiori", name: "灯織" }],
  portrait: async () => "data:image/png;base64,",
  play: (visible, ambient) => played.push([visible, ambient]),
  react: () => reactions++, destroy: () => destroyed++,
};
const visitor = createChibiVisitor(area, {
  random: () => .5, now: () => clock,
  schedule: (next, ms) => { timer = next; delay = ms; return 1; },
  cancel: () => { timer = null; },
  openActor: (_, __, selected) => { starts++; exclude = selected; return new Promise(resolve => { release = resolve; }); },
  onHide: () => hidden++,
  onMotionChange: value => visitor.setReducedMotion(value),
});
visitor.start();
visitor.start();
assert.equal(delay, 2_400_000);
clock = 600_000;
show(false);
assert.equal(timer, null);
clock = 9_000_000;
show(true);
assert.equal(delay, 1_800_000);
clock += delay;
const arriving = timer();
timer = null;
show(false);
show(true);
assert.equal(timer, null);
assert.equal(starts, 1);
release(actor);
await arriving;

const [root] = doc.body.childNodes;
assert.deepEqual(root.style, { width: "148px", height: "168px", left: "186px", top: "106px" });
const [sprite, controls, menu, picker] = root.childNodes;
const [swap, gear, close] = controls.childNodes;
assert.equal(gear.getAttribute("popovertarget"), "chibi-settings");
assert.equal(swap.getAttribute("popovertarget"), "chibi-picker");
const pointerDown = () => sprite.listeners.pointerdown[0]({
  isPrimary: true, button: 0, pointerId: 1, clientX: 200, clientY: 110,
});
const click = (detail = 1) => sprite.listeners.click[0]({ detail });
pointerDown();
sprite.listeners.pointermove[0]({ pointerId: 1, clientX: 203, clientY: 111 });
sprite.listeners.lostpointercapture[0]();
assert.equal(root.style.left, "186px");
click();
assert.equal(reactions, 1);
pointerDown();
sprite.listeners.pointermove[0]({ pointerId: 2, clientX: 12, clientY: 12 });
assert.equal(root.style.left, "186px");
sprite.listeners.pointermove[0]({ pointerId: 1, clientX: 999, clientY: 999 });
assert.equal(root.style.left, "272px");
assert.equal(root.style.top, "152px");
sprite.listeners.lostpointercapture[0]();
click();
assert.equal(reactions, 1);
click(0);
assert.equal(reactions, 2);
areaBox = { left: 100, top: 60, right: 190, bottom: 120 };
resize();
assert.equal(root.style.top, "60px");
assert.ok(parseFloat(root.style.left) + parseFloat(root.style.width) <= 190);
show(false);
show(true);
motion.matches = true;
systemMotionChanged();
motion.matches = false;
systemMotionChanged();
assert.deepEqual(played, [[true, true], [false, true], [true, true], [true, false], [true, true]]);
const beforeSwitch = { ...root.style };
const motionSwitch = menu.firstChild;
motionSwitch.listeners.click[0]({ currentTarget: motionSwitch });
assert.deepEqual(played.at(-1), [true, false]);
assert.equal(motionSwitch.getAttribute("aria-checked"), "true");
picker.listeners.toggle[0]({ newState: "open" });
const [, tile] = picker.firstChild.childNodes;
const switching = tile.listeners.click[0]();
assert.equal(exclude, "hiori");
await tile.listeners.click[0]();
assert.equal(starts, 2);
const hiori = { ...actor, key: "hiori", canvas: doc.createElement("canvas") };
release(hiori);
await switching;
assert.equal(sprite.firstChild, hiori.canvas);
assert.deepEqual(root.style, beforeSwitch);
assert.equal(destroyed, 1);
assert.equal(hidden, 0);
menu.lastChild.listeners.click[0]({ currentTarget: menu.lastChild });
assert.equal(hidden, 1);
close.listeners.click[0]();
assert.equal(reactions, 2);
assert.equal(hidden, 1);
assert.equal(doc.body.childNodes.length, 0);
assert.equal(destroyed, 2);
assert.equal(delay, 2_400_000);

const late = timer();
visitor.stop();
release(actor);
await late;
assert.equal(doc.body.childNodes.length, 0);
assert.equal(destroyed, 3);
assert.equal(visibility.size, 0);
assert.equal(systemMotionChanged, null);

visitor.start({ preview: true });
assert.equal(delay, 0);
const preview = timer();
release(actor);
await preview;
assert.equal(doc.body.childNodes.length, 1);
visitor.stop();
"""
        result = subprocess.run(["node", "--input-type=module", "-"], input=script, cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
