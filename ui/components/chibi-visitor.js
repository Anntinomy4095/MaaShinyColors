import { h, icon } from "./dom.js?v=8";
import { ensureStyles } from "./task-add.js";
import { createChibiPicker } from "./chibi-picker.js";

const MINUTE = 60_000;
const MIN_WAIT = 25 * MINUTE;
const MAX_WAIT = 55 * MINUTE;
const DRAG_THRESHOLD = 5;
const loadActor = async (doc, random, selectedKey) => {
  const { createChibiActor } = await import("./chibi-actor.js?v=8");
  return createChibiActor(doc, random, selectedKey);
};

function switchControl(label, toggle) {
  return h("button", { type: "button", class: "toggle chibi-switch", role: "switch", "aria-checked": "false",
    onclick: event => toggle(event.currentTarget.getAttribute("aria-checked") !== "true") },
  h("span", { text: label }), h("span", { class: "toggle-track", "aria-hidden": "true" }, h("span", { class: "toggle-thumb" })));
}

function mountVisitor(area, doc, actor, random, { close, change, hide, reduce }) {
  ensureStyles();
  const win = doc.defaultView;
  const root = doc.createElement("div");
  const sprite = doc.createElement("button");
  root.className = "chibi-visitor";
  sprite.className = "chibi-drag";
  sprite.type = "button";
  sprite.setAttribute("aria-label", "让访客做动作");
  const control = (label, glyph, attrs) => h("button", {
    class: "chibi-control", type: "button", title: label, "aria-label": label, ...attrs,
  }, icon(glyph));
  const swap = control("切换偶像", "swap", { popovertarget: "chibi-picker", "aria-expanded": "false" });
  const gear = control("访客设置", "gear", { popovertarget: "chibi-settings", "aria-expanded": "false" });
  const dismiss = control("关闭偶像访客", "close", { onclick: close });
  const controls = h("div", { class: "chibi-controls" }, swap, gear, dismiss);
  const motion = switchControl("减少动画", reduce);
  const menu = h("div", { class: "task-menu chibi-settings", id: "chibi-settings", popover: "auto",
    role: "group", "aria-label": "访客设置" });
  motion.setAttribute("autofocus", "");
  menu.append(motion, switchControl("不再显示", hide));
  const picker = createChibiPicker(change);
  function placeMenu(panel, button) {
    if (!panel.matches(":popover-open")) return;
    const anchor = button.getBoundingClientRect();
    const left = Math.max(4, Math.min(anchor.right - panel.offsetWidth, win.innerWidth - panel.offsetWidth - 4));
    const below = anchor.bottom + 4;
    const above = anchor.top - panel.offsetHeight - 4;
    const top = below + panel.offsetHeight <= win.innerHeight - 4 ? below : above;
    panel.style.left = `${left}px`;
    panel.style.top = `${Math.max(4, top)}px`;
  }
  menu.addEventListener("toggle", event => {
    gear.setAttribute("aria-expanded", String(event.newState === "open"));
    placeMenu(menu, gear);
  });
  picker.panel.addEventListener("toggle", event => {
    const open = event.newState === "open";
    swap.setAttribute("aria-expanded", String(open));
    if (open) {
      picker.show(actor);
      placeMenu(picker.panel, swap);
    }
  });
  sprite.append(actor.canvas);
  root.append(sprite, controls, menu, picker.panel);
  doc.body.append(root);

  let x, y, drag, dragged = false;
  function place(nextX = x, nextY = y) {
    const rect = area.getBoundingClientRect();
    const left = Math.max(0, rect.left), top = Math.max(0, rect.top);
    const right = Math.max(left, Math.min(win.innerWidth, rect.right));
    const bottom = Math.max(top, Math.min(win.innerHeight, rect.bottom));
    const { width, height } = actor;
    const w = Math.min(width, right - left, (bottom - top) * width / height);
    const h = w * height / width;
    const maxX = right - w, maxY = bottom - h;
    x = Math.max(left, Math.min(maxX, nextX ?? left + random() * (maxX - left)));
    y = Math.max(top, Math.min(maxY, nextY ?? top + random() * (maxY - top)));
    root.style.width = `${w}px`;
    root.style.height = `${h}px`;
    root.style.left = `${x}px`;
    root.style.top = `${y}px`;
    placeMenu(menu, gear);
    placeMenu(picker.panel, swap);
  }
  sprite.addEventListener("pointerdown", event => {
    if (drag || !event.isPrimary || event.button !== 0) return;
    dragged = false;
    sprite.setPointerCapture(event.pointerId);
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: x, top: y };
  });
  sprite.addEventListener("pointermove", event => {
    if (drag?.id !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!dragged && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    dragged = true;
    sprite.classList.add("dragging");
    place(drag.left + dx, drag.top + dy);
  });
  sprite.addEventListener("lostpointercapture", () => {
    drag = null;
    sprite.classList.remove("dragging");
  });
  sprite.addEventListener("click", event => {
    if (!dragged || event.detail === 0) actor.react();
  });
  const resize = () => place();
  const observer = new win.ResizeObserver(resize);
  observer.observe(area);
  win.addEventListener("resize", resize);
  place();

  return {
    get key() { return actor.key; },
    play(visible, ambient) { actor.play(visible, ambient); },
    motion(value) {
      motion.setAttribute("aria-checked", String(value));
      motion.classList.toggle("is-on", value);
    },
    loading(value) { swap.disabled = value; },
    replace(next) {
      actor.destroy();
      actor = next;
      sprite.replaceChildren(actor.canvas);
    },
    destroy() {
      observer.disconnect();
      win.removeEventListener("resize", resize);
      actor.destroy();
      root.remove();
    },
  };
}

export function createChibiVisitor(area, {
  doc = area.ownerDocument, random = Math.random, now = () => performance.now(),
  schedule = setTimeout, cancel = clearTimeout, openActor = loadActor, onHide = () => {}, onMotionChange = () => {},
} = {}) {
  let active = null, timer = null, deadline = 0, remaining = 0, running = false;
  let generation = 0, loading = false, reduceMotion = false;
  const drawWait = () => MIN_WAIT + random() * (MAX_WAIT - MIN_WAIT);
  const win = doc.defaultView;
  const motion = win.matchMedia("(prefers-reduced-motion: reduce)");
  const syncMotion = () => active?.play(!doc.hidden, !reduceMotion && !motion.matches);
  function arm() {
    if (!running || doc.hidden || timer !== null || loading) return;
    deadline = now() + remaining;
    timer = schedule(visit, remaining);
  }
  function cancelWait() {
    if (timer === null) return;
    cancel(timer);
    timer = null;
    remaining = Math.max(0, deadline - now());
  }
  function dismiss() {
    generation++;
    loading = false;
    active?.destroy();
    active = null;
    remaining = drawWait();
    arm();
  }
  function visit() {
    timer = null;
    remaining = 0;
    return showActor();
  }
  async function showActor(selectedKey) {
    if (!running || doc.hidden || loading || (selectedKey && selectedKey === active?.key)) return;
    const pending = generation;
    const current = active;
    loading = true;
    current?.loading(true);
    let actor;
    try {
      actor = await openActor(doc, random, selectedKey);
      if (pending !== generation) { actor?.destroy(); return; }
      if (!actor) { if (!current) stop(); return; }
      if (current) current.replace(actor);
      else active = mountVisitor(area, doc, actor, random,
        { close: dismiss, change: showActor, hide: onHide, reduce: onMotionChange });
      active.motion(reduceMotion);
      syncMotion();
    } catch {
      if (pending !== generation) return;
      if (!active) actor?.destroy();
      if (!current) stop();
    } finally {
      if (pending === generation) {
        loading = false;
        current?.loading(false);
      }
    }
  }
  function visibility() {
    if (active) { syncMotion(); return; }
    if (doc.hidden) cancelWait();
    else arm();
  }
  function stop() {
    running = false;
    generation++;
    loading = false;
    cancelWait();
    active?.destroy();
    active = null;
    doc.removeEventListener("visibilitychange", visibility);
    motion.removeEventListener("change", syncMotion);
  }
  return {
    setReducedMotion(value) {
      if (value === reduceMotion) return;
      reduceMotion = value;
      active?.motion(value);
      syncMotion();
    },
    start({ preview = false } = {}) {
      if (running) return;
      running = true;
      remaining = preview ? 0 : drawWait();
      doc.addEventListener("visibilitychange", visibility);
      motion.addEventListener("change", syncMotion);
      arm();
    },
    stop,
  };
}
