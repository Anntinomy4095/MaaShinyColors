import { h } from "./dom.js";
import { ensureStyles } from "./office-eggs-view.js";
import { createOfficeNativeStage } from "./office-eggs-native.js?v=6";

export const OFFICE_VISITOR_DELAY_MS = 3 * 60 * 1000;

// Only visible window time counts. One visitor per opening of the application; no game input.
export function createOfficeVisitor({ onDiscover = () => {}, isOpen = () => false,
  delayMs = OFFICE_VISITOR_DELAY_MS, doc = globalThis.document, clock = Date.now,
  setTimer = setInterval, clearTimer = clearInterval } = {}) {
  let native = null;
  let timer = null, root = null, elapsed = 0, lastAt = clock(), finished = false;
  function stop() {
    if (timer !== null) clearTimer(timer);
    timer = null; native?.destroy(); native = null; root?.remove(); root = null;
  }
  function dismiss() { finished = true; stop(); doc.removeEventListener?.("visibilitychange", visibility); }
  function arrive() {
    if (finished || root || isOpen()) return;
    if (timer !== null) clearTimer(timer);
    timer = null; ensureStyles();
    root = h("div", { class: "egg-visitor" },
      h("button", { type: "button", class: "egg-visitor-bird", "aria-label": "跟着鸽子去事务所", title: "事务所",
        onclick: () => { dismiss(); onDiscover(); } },
        h("img", { src: new URL("../assets/office/dove-flight.svg", import.meta.url).href, alt: "", class: "egg-visitor-fly" }),
        h("img", { src: new URL("../assets/office/dove-rest.svg", import.meta.url).href, alt: "", class: "egg-visitor-rest" })),
      h("button", { type: "button", class: "egg-visitor-dismiss", "aria-label": "让鸽子离开", onclick: dismiss }, "×"));
    doc.body.append(root);
    const surface = root.querySelector?.(".egg-visitor-bird");
    if (surface) native = createOfficeNativeStage({ stage: surface, doc, width: 64, height: 64, birdWidth: 42,
      onReady: () => {
        if (!native?.hasBirds) return;
        surface.querySelectorAll("img").forEach((img) => { img.hidden = true; });
        native.draw({ story: { actors: [], playing: true }, flock: { mode: "resting", feeds: 0, flights: 0 },
          birds: [{ id: 1, x: .5, y: .82, pose: "idle", scale: 1, facing: -1, opacity: 1 }] });
      } });
    native?.draw({ story: { actors: [], playing: true }, flock: { mode: "resting", feeds: 0, flights: 0 },
      birds: [{ id: 1, x: .5, y: .82, pose: "fly", scale: 1, facing: -1, opacity: 1 }] });
  }
  function tick() {
    const now = clock();
    if (isOpen()) { dismiss(); return; }
    if (!doc.hidden) elapsed += Math.max(0, now - lastAt);
    lastAt = now;
    if (elapsed >= delayMs) arrive();
  }
  function visibility() { lastAt = clock(); }
  function start() {
    if (timer !== null || root || finished) return;
    lastAt = clock(); timer = setTimer(tick, 1000);
    doc.addEventListener?.("visibilitychange", visibility);
  }
  return { start, arrive, tick, stop: () => { dismiss(); doc.removeEventListener?.("visibilitychange", visibility); },
    get elapsedMs() { return elapsed; }, get arrived() { return Boolean(root); } };
}
