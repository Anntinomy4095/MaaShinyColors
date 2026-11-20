// 布局 (2026-10-25, user: a wider panel should change its layout instead of stretching buttons). 双栏 = the tab rail on
// the left and, on each page, a section list beside the selected section (styles/wide.css, every rule under
// .app[data-wide]). 自动 switches to it from WIDE_MIN px; 双栏 applies from DOUBLE_MIN px. From ASIDE_MIN px a third column
// keeps 开始 / 停止 and the log in view (.app[data-aside]; chose this 工作台 layout).
// Kept per device in localStorage, not in the profile: the same profile opens in the narrow dock and in a wide window.
import { loadLocal, saveLocal } from "./bridge.js";

export const LAYOUTS = [["auto", "自动"], ["single", "单栏"], ["double", "双栏"]];
export const WIDE_MIN = 880;
export const DOUBLE_MIN = 640;
export const ASIDE_MIN = 1200;
const KEY = "sc-ui-layout";

export const layoutMode = (value) => (LAYOUTS.some(([k]) => k === value) ? value : "auto");
export const loadLayout = () => layoutMode(loadLocal(KEY));
export const saveLayout = (mode) => saveLocal(KEY, layoutMode(mode));

/** Whether a panel `width` CSS px wide uses the 双栏 layout under `mode` (pure). */
export function isWide(mode, width) {
  const m = layoutMode(mode);
  return m === "double" ? width >= DOUBLE_MIN : m === "auto" && width >= WIDE_MIN;
}

/** null (one column), "md" (rail + section list / detail) or "aside" (+ the run column) for a panel `width` px wide (pure). */
export function wideTier(mode, width) {
  if (!isWide(mode, width)) return null;
  return width >= ASIDE_MIN ? "aside" : "md";
}

/**
 * Keeps .app[data-wide] / [data-aside] in step with the app's width and the chosen mode and reports a tier change
 * (pages render their section list or the run column only in a wide tier); returns the re-apply function.
 */
export function watchLayout(app, getMode, onChange = () => {}) {
  let last;
  const apply = () => {
    const tier = wideTier(getMode(), app.clientWidth);
    if (tier) app.dataset.wide = ""; else delete app.dataset.wide;
    if (tier === "aside") app.dataset.aside = ""; else delete app.dataset.aside;
    if (tier !== last) { last = tier; onChange(tier); }
  };
  if (typeof ResizeObserver === "function") new ResizeObserver(apply).observe(app);
  apply();
  return apply;
}
