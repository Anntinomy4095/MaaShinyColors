// 2026-10-31 (user: 展开选项后无法向下滑动看后续的选项). A full render replaces the open panel and its scroll areas; one that
// lands during a wheel / touchpad scroll or a scrollbar drag drops the rest of that scroll. Status changes, log lines and
// readings arrive every second during a run (and every status tick on 设置 with 开发者选项), so those background changes
// now wait until the scrolling has stopped (at most HOLD_MAX_MS); a change the user made still renders at once.

/** Store change reasons that come from the host rather than from the user. */
export const BACKGROUND_REASONS = new Set(["status", "status-tick", "log", "readings", "history"]);
export const SCROLL_QUIET_MS = 400;   // a wheel notch's smooth scroll has finished by then
export const PRESS_MAX_MS = 5000;     // a press whose pointerup never arrived stops holding after this
export const HOLD_MAX_MS = 3000;      // the header and status bar never lag further behind the host

/** Wheel input and presses on a scroll area; `wait()` is how long a background render should still wait. */
export function scrollActivity({ quiet = SCROLL_QUIET_MS, pressMax = PRESS_MAX_MS, now = () => performance.now() } = {}) {
  let until = 0;
  let pressedAt = null;
  return {
    wheel() { until = now() + quiet; },
    press(onScroller) { if (onScroller) pressedAt = now(); },
    release() {
      if (pressedAt === null) return;
      pressedAt = null;
      until = now() + quiet;
    },
    wait() {
      const t = now();
      const press = pressedAt === null ? 0 : Math.min(quiet, pressMax - (t - pressedAt));
      return Math.max(0, press, until - t);
    },
  };
}

/**
 * One render per microtask for any number of store changes: `render` when any of them was not a volatile status tick,
 * else `renderTick`. Returns `enqueue(reason)`.
 */
export function renderQueue({ render, renderTick, activity, holdMax = HOLD_MAX_MS, now = () => performance.now(),
  schedule = (fn, ms) => setTimeout(fn, ms), cancel = (id) => clearTimeout(id), micro = (fn) => queueMicrotask(fn) }) {
  let pending = null;
  let timer = null;
  const flush = () => {
    if (!pending) return;
    const wait = pending.urgent ? 0 : Math.min(activity.wait(), Math.max(0, pending.since + holdMax - now()));
    if (wait > 0) {
      if (timer === null) timer = schedule(() => { timer = null; flush(); }, wait);
      return;
    }
    if (timer !== null) { cancel(timer); timer = null; }
    const { full } = pending;
    pending = null;
    if (full) render(); else renderTick();
  };
  return (reason) => {
    const first = !pending;
    if (first) pending = { full: false, urgent: false, since: now() };
    if (reason !== "status-tick") pending.full = true;
    const urgent = !BACKGROUND_REASONS.has(reason);
    if (urgent) pending.urgent = true;
    if (first || urgent) micro(flush);
  };
}

/** Feeds the user's wheel input and presses on a scroll area (its own box, i.e. the scrollbar) into `activity`. */
export function watchScrolling(doc, activity) {
  const opts = { capture: true, passive: true };
  doc.addEventListener("wheel", () => activity.wheel(), opts);
  doc.addEventListener("pointerdown", (event) => activity.press(Boolean(event.target?.matches?.("[data-scroll-key], .panel-scroll"))), opts);
  for (const type of ["pointerup", "pointercancel"]) doc.addEventListener(type, () => activity.release(), opts);
  doc.defaultView?.addEventListener("blur", () => activity.release());
}
