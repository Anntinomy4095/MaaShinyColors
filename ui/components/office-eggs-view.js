// 设置 → 关于 hidden 事务所 window: 真乃的鸽子 and the corner mini-stories. Tapping the version 5 times shows the switch;
// the window is a small floating panel over the UI. Logic and assets come from office-eggs-core.js / -assets.js (loaded
// only when the window opens); this file draws the snapshot and forwards taps. Nothing here talks to the game or host.
// Saved per device (localStorage): the switch and window position here, the core's exportState() under its own key.
import { h, clear } from "./dom.js";
import { remembered, remember } from "./common.js";
import { createOfficeNativeStage } from "./office-eggs-native.js?v=6";

export const PREFS_KEY = "sc-office-eggs-prefs";
export const UNLOCK_TAPS = 5;
export const UNLOCK_WINDOW_MS = 3000;
const STYLE_HREF = "styles/office-eggs.css?v=6";
const TICK_MS = 66;
const TICK_REDUCED_MS = 500;

const bool = (v) => v === true;
const finite = (v) => typeof v === "number" && Number.isFinite(v);

/** { unlocked, on, pos: {x, y} | null } from localStorage; anything malformed falls back to locked/off. */
export function loadEggPrefs() {
  const raw = remembered(PREFS_KEY, {});
  const pos = raw && finite(raw.pos?.x) && finite(raw.pos?.y) ? { x: raw.pos.x, y: raw.pos.y } : null;
  return { unlocked: bool(raw?.unlocked), on: bool(raw?.unlocked) && bool(raw?.on), pos };
}

export function saveEggPrefs(prefs) {
  remember(PREFS_KEY, { unlocked: bool(prefs.unlocked), on: bool(prefs.unlocked) && bool(prefs.on), pos: prefs.pos || null });
}

/** Click handler for the version value: `taps` quick taps (each within `windowMs` of the first) call onUnlock once. */
export function versionTapper(onUnlock, { taps = UNLOCK_TAPS, windowMs = UNLOCK_WINDOW_MS, now = () => Date.now() } = {}) {
  let first = 0;
  let count = 0;
  return () => {
    const t = now();
    if (!count || t - first > windowMs) { first = t; count = 0; }
    count += 1;
    if (count >= taps) { count = 0; onUnlock(); }
  };
}

/** 关于 row once unlocked: 「事务所」 and one switch. */
export function eggSwitchRow(prefs, actions) {
  if (!prefs.unlocked) return null;
  return h("div", { class: "egg-switch-row" },
    h("span", { id: "about-office-eggs-label", text: "事务所" }),
    h("button", { class: `toggle${prefs.on ? " is-on" : ""}`, type: "button", role: "switch", "aria-checked": String(prefs.on),
      "aria-labelledby": "about-office-eggs-label", dataset: { focusKey: "about-office-eggs" },
      onclick: () => actions.officeEggs?.(!prefs.on) },
      h("span", { class: "toggle-track" }, h("span", { class: "toggle-thumb" }))));
}

export function ensureStyles() {
  if (typeof document === "undefined" || document.querySelector(`link[href="${STYLE_HREF}"]`)) return;
  document.head.append(h("link", { rel: "stylesheet", href: STYLE_HREF }));
}

const STAGE_W = 336;
const STAGE_H = 189;
const ACTOR_H = 104;
const PROP_W = { parcel: 52, controller: 26, gift: 28, sleep: 22, camera: 27, castle: 76, flag: 25,
  cup: 32, tea: 28, cookie: 25, rainbow: 40, paper: 31, book: 54, leaf: 24, music: 24, stone: 25, sparkle: 27 };
const BIRD_W = 28;
const SEEDS = [[0, 0], [-0.02, 0.012], [0.018, 0.008], [-0.008, -0.01], [0.012, -0.014]];
// Reaction kind -> who reacts and the one short mark shown over them (no sentences).
const REACTIONS = {
  mun: { actor: "mano" }, wobble: { prop: "parcel" }, peek: { actor: "tenka" },
  play: { prop: "controller" }, sparkle: { prop: "gift" }, snooze: { prop: "sleep" },
};

/** Fraction of the stage (0..1) under a pointer. */
export function stagePoint(rect, clientX, clientY) {
  return { x: (clientX - rect.left) / (rect.width || STAGE_W), y: (clientY - rect.top) / (rect.height || STAGE_H) };
}

/** A tap on a bird scatters the flock; anywhere else feeds there. */
export function tapIntent(snap, x, y) {
  const hit = (snap?.birds || []).some((b) => Math.abs(b.x - x) < 0.06 && y <= b.y + 0.02 && y >= b.y - 0.1);
  return hit ? "scatter" : "feed";
}

/** Window position kept inside the viewport so the title bar stays reachable. */
export function clampPos(pos, view = { w: globalThis.innerWidth || 1280, h: globalThis.innerHeight || 720 }) {
  if (!pos) return null;
  return { x: Math.round(Math.min(Math.max(0, pos.x), Math.max(0, view.w - 120))),
    y: Math.round(Math.min(Math.max(0, pos.y), Math.max(0, view.h - 32))) };
}

function setPlace(el, x, y, extra = "") {
  el.style.left = `${(x * 100).toFixed(3)}%`;
  el.style.top = `${(y * 100).toFixed(3)}%`;
  el.style.transform = `translate(-50%, -100%)${extra}`;
}

/**
 * The floating 事务所 window. open() loads the core and art, mounts the window and starts drawing; close() stops drawing
 * and removes it. onClose: the × button. onMove(pos): after a drag.
 */
export function createEggWindow({ onClose = () => {}, onMove = () => {}, pos = null,
  loadCore = () => import("./office-eggs-core.js?v=4"),
  loadAssets = () => import("./office-eggs-assets.js").then((m) => m.loadOfficeAssets()),
  clock = () => Date.now(), random = Math.random, doc = globalThis.document, container = null, persist = true } = {}) {
  let native = null;
  let root = null;
  let stage = null;
  let dots = null;
  let title = null;
  let eggs = null;
  let art = {};
  let timer = null;
  let token = 0;
  let place = pos;
  let savedState = null;
  const nodes = new Map();

  const reduced = () => doc?.documentElement?.dataset?.motion === "reduce" || Boolean(globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);

  function node(key, make) {
    let el = nodes.get(key);
    if (!el) { el = make(); nodes.set(key, el); stage.append(el); }
    el.dataset.seen = "1";
    return el;
  }

  function sprite(key, artKey, cls, width) {
    return node(key, () => {
      const src = art[artKey]?.src;
      const el = src ? h("img", { class: `egg-sprite ${cls}`, src, alt: "", draggable: "false" })
        : h("div", { class: `egg-sprite ${cls} egg-fallback` });
      if (width) el.style.width = `${width / STAGE_W * 100}%`;
      return el;
    });
  }

  function draw(snap) {
    if (!stage || !snap) return;
    native?.draw(snap);
    for (const el of nodes.values()) el.dataset.seen = "";
    const kind = snap.reaction?.kind || null;
    const react = kind ? REACTIONS[kind] || null : null;
    let markAt = null;
    for (const p of snap.story?.props || []) {
      const artKey = p.asset || p.key;
      const el = sprite(`prop:${p.key}`, artKey, `egg-prop egg-prop-${artKey}`, PROP_W[artKey] || 28);
      el.dataset.key = p.key;
      el.dataset.motion = p.motion || "";
      el.style.zIndex = String(p.layer ?? 3);
      const reacting = snap.reaction?.target ? snap.reaction.target === p.key : react?.prop === p.key;
      el.classList.toggle("react", reacting);
      setPlace(el, p.x, p.y);
      if (reacting && react?.mark) markAt = { x: p.x, y: p.y - 0.26 };
    }
    for (const a of snap.story?.actors || []) {
      if (native?.has(a.key) || !art[a.key]) continue;   // character art is optional; props and birds still play
      const el = sprite(`actor:${a.key}`, a.key, "egg-actor", Math.round(ACTOR_H * 134 / 224));
      el.style.zIndex = String(a.layer ?? 2);
      for (const pose of ["stand", "peek", "arrive", "sleep", "lean", "beat", "offbeat"]) el.classList.toggle(`pose-${pose}`, (a.pose || "stand") === pose);
      el.classList.toggle("react", react?.actor === a.key);
      setPlace(el, a.x, a.y);
      if (react?.actor === a.key && react.mark) markAt = { x: a.x, y: a.y - (a.pose === "peek" ? 0.34 : 0.58) };
    }
    if (react?.mark && !markAt) markAt = { x: 0.5, y: 0.4 };
    const food = snap.flock?.food;
    if (food) SEEDS.slice(0, Math.ceil(SEEDS.length * (food.remaining ?? 1))).forEach(([dx, dy], i) => {
      const el = node(`seed:${i}`, () => h("span", { class: "egg-seed" }));
      setPlace(el, food.x + dx, food.y + dy);
    });
    for (const b of native?.hasBirds ? [] : snap.birds || []) {
      const flying = ["fly", "takeoff"].includes(b.pose);
      const artKey = flying && !reduced() && art.pigeonFly ? "pigeonFly" : "pigeon";
      const el = sprite(`bird:${b.id}`, artKey, "egg-bird", Math.round(BIRD_W * (b.scale || 1)));
      if (art[artKey]?.src && el.getAttribute("src") !== art[artKey].src) el.setAttribute("src", art[artKey].src);
      el.style.zIndex = "5";
      el.style.opacity = String(b.opacity ?? 1);
      el.dataset.pose = b.pose;
      el.classList.toggle("flying", flying);
      setPlace(el, b.x, b.y + (b.bob || 0),
        ` rotate(${(b.rotation || 0).toFixed(1)}deg) scale(${(b.facing === -1 ? -1 : 1) * (b.squashX || 1)},${b.squashY || 1})`);
      if (b.shadow) {
        const shadow = node(`shadow:${b.id}`, () => h("span", { class: "egg-shadow" }));
        shadow.style.opacity = String(b.shadow.opacity);
        setPlace(shadow, b.shadow.x, b.shadow.y, ` scaleX(${b.shadow.scale})`);
      }
    }
    if (markAt) {
      const el = node("mark", () => h("span", { class: "egg-bubble" }));
      el.textContent = react.mark;
      el.style.zIndex = "6";
      setPlace(el, markAt.x, Math.max(0.14, markAt.y));
    }
    for (const [key, el] of nodes) if (!el.dataset.seen) { el.remove(); nodes.delete(key); }
    const total = snap.story?.total || 0;
    if (dots.childNodes.length !== total) { clear(dots); for (let i = 0; i < total; i += 1) dots.append(h("i", {})); }
    dots.childNodes.forEach((d, i) => d.classList.toggle("on", i <= (snap.story?.index ?? -1)));
    title.textContent = "";
  }

  function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }

  function startTimer() {
    stopTimer();
    timer = setInterval(() => { if (eggs && !doc?.hidden) draw(eggs.snapshot()); }, reduced() ? TICK_REDUCED_MS : TICK_MS);
  }

  const act = (fn) => { if (eggs) draw(fn(eggs)); };
  const visibility = () => eggs?.setActive(!doc.hidden);

  function onStageClick(event) {
    const prop = event.target?.dataset?.key;
    if (prop) { act((e) => e.tapProp(prop)); return; }
    const { x, y } = stagePoint(stage.getBoundingClientRect(), event.clientX, event.clientY);
    act((e) => ((native?.hasBirds ? native.hitBird(x, y) : tapIntent(e.snapshot(), x, y) === "scatter") ? e.scatter() : e.feed(x, y)));
  }

  function onStageKey(event) {
    if (event.key === "Enter") { event.preventDefault(); act((e) => e.feed()); }
    else if (event.key === " ") { event.preventDefault(); act((e) => e.scatter()); }
  }

  function applyPlace() {
    if (!root || !place) return;
    root.style.left = `${place.x}px`;
    root.style.top = `${place.y}px`;
    root.style.bottom = "auto";
  }

  function dragFrom(event) {
    if (event.button !== 0 || event.target?.closest?.("button")) return;
    const bar = event.currentTarget;
    const start = { x: event.clientX, y: event.clientY, left: root.offsetLeft, top: root.offsetTop };
    bar.classList.add("dragging");
    bar.setPointerCapture?.(event.pointerId);
    const move = (e) => {
      place = clampPos({ x: start.left + e.clientX - start.x, y: start.top + e.clientY - start.y });
      applyPlace();
    };
    const up = () => {
      bar.classList.remove("dragging");
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", up);
      bar.removeEventListener("pointercancel", up);
      if (place) onMove(place);
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up);
    bar.addEventListener("pointercancel", up);
  }

  async function open() {
    if (root) return;
    const mine = ++token;
    ensureStyles();
    const [core, loaded] = await Promise.all([loadCore(), loadAssets().catch(() => null)]);
    if (mine !== token || root) return;
    art = loaded?.assets || {};
    eggs = new core.OfficeEggs({ saved: savedState || (persist ? remembered(core.OFFICE_EGGS_STORAGE_KEY, null) : null), clock, random,
      onSave: (state) => { savedState = state; if (persist) remember(core.OFFICE_EGGS_STORAGE_KEY, state); } });
    dots = h("span", { class: "egg-dots", "aria-hidden": "true" });
    stage = h("div", { class: "egg-stage", tabindex: "0", "aria-label": "事务所", onclick: onStageClick, onkeydown: onStageKey });
    title = h("span", { class: "egg-title", "aria-hidden": "true" });
    root = h("div", { class: "egg-window", role: "dialog", "aria-label": "事务所" },
      h("div", { class: "egg-bar", onpointerdown: dragFrom },
        title, dots,
        h("button", { type: "button", "aria-label": "关闭", title: "关闭", dataset: { focusKey: "office-eggs-close" },
          onclick: () => onClose() }, "×")),
      stage);
    place = container ? null : clampPos(place);
    applyPlace();
    (container || doc.body).append(root);
    native = createOfficeNativeStage({ stage, doc, onReady: () => { if (eggs) draw(eggs.snapshot()); } });
    draw(eggs.open());
    eggs.setActive(!doc.hidden);
    doc.addEventListener?.("visibilitychange", visibility);
    startTimer();
  }

  function close() {
    token += 1;
    stopTimer();
    native?.destroy(); native = null;
    eggs?.close();
    doc.removeEventListener?.("visibilitychange", visibility);
    eggs = null;
    nodes.clear();
    root?.remove();
    root = stage = dots = title = null;
  }

  return { open, close, refresh: () => { if (eggs) draw(eggs.snapshot()); },
    setSpeed: (value) => native?.setSpeed(value), get nativeMetrics() { return native?.metrics; },
    get isOpen() { return Boolean(root); }, get eggs() { return eggs; } };
}
